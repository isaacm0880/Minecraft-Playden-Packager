"""PS5 receiver section: Qt owns UI state; workers own deployment and network I/O."""
from pathlib import Path
import re
from dataclasses import replace

from PySide6.QtCore import QSettings, QThread, QTimer, Signal, Slot, Qt
from PySide6.QtWidgets import QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QLayout, QVBoxLayout, QWidget

from .gui_components import Task, AdvancedSection
from .network_protocol import lan_address, NetworkTransferError
from .operations import CancellationToken
from .receiver_manager import Deployment, ReceiverConfiguration, PairedReceiver, ReceiverInfo, ReceiverError, export_receiver_elf, identify, pair_receiver, receiver_status, stop_receiver


class ReceiverManager(QGroupBox):
    credentials_changed = Signal()
    state_changed = Signal(str)

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__('Connect to PS5', parent)
        self.settings = settings
        self.state = 'Unknown'
        self.paired: PairedReceiver | None = None
        self.info: ReceiverInfo | None = None
        self.deployment: Deployment | None = None
        self._owned_receiver: PairedReceiver | None = None
        self.worker: QThread | None = None
        self.task: Task | None = None
        self.cancellation = CancellationToken()
        self.operation = ''
        self.transfer_active = False
        self.monitoring = True
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        form = QFormLayout()
        self.host = QLineEdit()
        try:
            self.host.setText(lan_address(str(settings.value('transfer/host', ''))))
        except ValueError:
            pass
        self.host.setPlaceholderText('192.168.1.50')
        form.addRow('PS5 IP address', self.host)
        row = QHBoxLayout()
        self.start_button = QPushButton('Start Receiver')
        self.stop_button = QPushButton('Stop Receiver')
        self.refresh_button = QPushButton('Refresh status')
        self.cancel_button = QPushButton('Cancel local wait')
        for button in (self.start_button, self.stop_button, self.refresh_button, self.cancel_button):
            row.addWidget(button)
        form.addRow(row)
        self.status = QLabel('Receiver: Unknown')
        self.version = QLabel('Version: unknown • Listening port: unknown')
        self.error = QLabel()
        for display in (self.status, self.version, self.error):
            display.setTextFormat(Qt.TextFormat.PlainText)
            display.setWordWrap(True)
            display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(display)
        self.advanced = AdvancedSection('Transfer advanced options')
        self.code = QLineEdit()
        self.code.setMaxLength(6)
        self.code.setMaximumWidth(150)
        self.code.setPlaceholderText('6 digits from PS5')
        self.code.setToolTip('Expires after five minutes or five attempts. Kept in memory only, never saved or logged.')
        self.pair_button = QPushButton('Pair')
        pair = QHBoxLayout(); pair.addWidget(self.code); pair.addWidget(self.pair_button); pair.addStretch()
        self.advanced.form.addRow('Manual pairing code', pair)
        layout.addLayout(form)
        note = QLabel('Select a package → Start Receiver → Pair if required → Send to PS5. '
                      'Start uses an already running ELF loader (normally TCP 9021). '
                      'Stop applies only to this authenticated, idle receiver; it never restarts the PS5. '
                      'Closing this app does not stop the receiver.')
        note.setWordWrap(True)
        self.advanced.form.addRow(note)
        self.configuration = QWidget()
        configuration = QFormLayout(self.configuration)
        config = ReceiverConfiguration.load(settings)
        self.sdk = QLineEdit(str(config.sdk))
        self.elf = QLineEdit(str(config.elf))
        for label, field, picker in (('PS5 Payload SDK', self.sdk, self.choose_sdk), ('Receiver ELF', self.elf, self.choose_elf)):
            row = QHBoxLayout(); row.addWidget(field)
            button = QPushButton('Choose…'); button.clicked.connect(picker); row.addWidget(button)
            configuration.addRow(label, row)
        self.loader_port = QSpinBox(); self.data_port = QSpinBox(); self.control_port = QSpinBox()
        for label, box, key, default in (('ELF loader port', self.loader_port, 'loaderPort', config.loader_port),
                                         ('Receiver listening port', self.data_port, 'dataPort', config.port),
                                         ('Receiver control port', self.control_port, 'controlPort', config.control_port)):
            box.setRange(1, 65535)
            try:
                box.setValue(int(str(settings.value('receiver/' + key, default))))
            except (ValueError, TypeError):
                box.setValue(default)
            box.valueChanged.connect(lambda value, key=key: settings.setValue('receiver/' + key, value))
            configuration.addRow(label, box)
        self.advanced.form.addRow(self.configuration)
        self.export_button = QPushButton('Export PS5 Receiver (.elf)…')
        self.export_button.clicked.connect(self.export_elf)
        self.advanced.form.addRow(self.export_button)
        layout.addWidget(self.advanced)
        self.start_button.clicked.connect(self.start_receiver)
        self.stop_button.clicked.connect(self.stop_receiver)
        self.pair_button.clicked.connect(self.pair)
        self.refresh_button.clicked.connect(self.refresh)
        self.cancel_button.clicked.connect(self.cancel)
        self.host.textChanged.connect(self.connection_changed)
        self.control_port.valueChanged.connect(self.connection_changed)
        self.code.textChanged.connect(self.update_controls)
        self.sdk.textChanged.connect(lambda value: settings.setValue('receiver/sdk', value))
        self.elf.textChanged.connect(lambda value: settings.setValue('receiver/elf', value))
        self.monitor = QTimer(self)
        self.monitor.setInterval(5000)
        self.monitor.timeout.connect(self.poll)
        self.update_controls()

    @property
    def ready(self) -> bool:
        return (self.state == 'Running' and self.paired is not None and self.info is not None
                and not self.info.busy and self.operation not in ('start', 'pair', 'stop'))

    def busy(self) -> bool:
        return self.worker is not None

    @Slot()
    def connection_changed(self) -> None:
        self.paired = None
        if (self._owned_receiver is not None and self.host.text().strip() == self._owned_receiver.host
                and self.control_port.value() == self._owned_receiver.info.control_port):
            # Restore only the in-memory owner for this endpoint; Refresh must
            # authenticate its identity before any action becomes available.
            self.paired = self._owned_receiver
        self.info = None
        self.code.clear()
        self.monitor.stop()
        self.set_state('Unknown')
        self.version.setText('Version: unknown • Listening port: unknown')
        self.credentials_changed.emit()

    def set_state(self, state: str, message: str = '') -> None:
        self.state = state
        self.status.setText('Receiver: ' + state)
        self.error.setText(message)
        self.update_controls()
        self.state_changed.emit(state)
        self.credentials_changed.emit()

    @Slot()
    def update_controls(self) -> None:
        valid_host = False
        try:
            lan_address(self.host.text().strip())
            valid_host = True
        except ValueError:
            pass
        idle = not self.busy() and not self.transfer_active
        self.start_button.setEnabled(idle and valid_host and self.state != 'Running')
        self.stop_button.setEnabled(idle and self.ready and self.info is not None and not self.info.busy)
        self.stop_button.setToolTip('Finish or cancel active transfers first. Stop never interrupts a transfer or kills unrelated payloads.')
        self.refresh_button.setEnabled(idle and valid_host)
        self.pair_button.setEnabled(idle and valid_host and not (self.state == 'Running' and self.paired is not None) and re.fullmatch(r'[0-9]{6}', self.code.text()) is not None)
        self.code.setReadOnly(self.state == 'Running' and self.paired is not None)
        self.cancel_button.setEnabled(self.busy() and self.operation != 'stop' and not self.cancellation.cancelled)
        self.export_button.setEnabled(idle)
        for field in (self.host, self.code, self.configuration):
            field.setEnabled(idle)

    def install(self, paired: PairedReceiver, *, monitor: bool = True) -> None:
        self.paired = paired
        self.info = paired.info
        self.data_port.setValue(paired.info.port)
        if paired.pairing_code is not None:
            self.code.setText(paired.pairing_code)
        self.version.setText(f'Version: {paired.info.version} • Listening port: {paired.info.port} • Control port: {paired.info.control_port}')
        self.set_state('Running', 'Authenticated. Receiver is busy with a transfer.' if paired.info.busy else 'Authenticated and ready to send a package.')
        if monitor and self.monitoring:
            self.monitor.start()

    def set_transfer_active(self, active: bool) -> None:
        was_active = self.transfer_active
        self.transfer_active = active
        self.update_controls()
        if was_active and not active and self.monitoring and self.paired is not None:
            self.refresh()

    def _work(self, operation: str, callback) -> None:
        if self.busy() or self.transfer_active:
            return
        self.operation = operation
        self.cancellation = CancellationToken()
        self.worker = QThread(self)
        def run(progress):
            try:
                return callback(progress)
            except NetworkTransferError:
                raise
            except OSError as exc:
                if operation == 'export':
                    raise ReceiverError(f'Could not export receiver ELF: {exc}') from exc
                raise ReceiverError('Receiver connection failed or was lost. Check the PS5 IP, ports and LAN. '
                                    'The receiver may have stopped; Refresh or pair with its new code after a restart.') from exc
            except ValueError as exc:
                raise ReceiverError(str(exc)) from exc
        self.task = Task(run, self.cancellation)
        self.task.moveToThread(self.worker)
        self.worker.started.connect(self.task.run)
        self.task.result.connect(self._result)
        self.task.error.connect(self._error)
        self.task.cancelled.connect(self._cancelled)
        self.task.finished.connect(self.worker.quit)
        self.task.finished.connect(self.task.deleteLater)
        self.worker.finished.connect(self._finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.update_controls()
        self.credentials_changed.emit()
        self.worker.start()

    @Slot()
    def start_receiver(self) -> None:
        if self.busy() or self.transfer_active or self.state == 'Running':
            return
        host, sdk, elf = self.host.text().strip(), Path(self.sdk.text()).expanduser(), Path(self.elf.text()).expanduser()
        ports = self.loader_port.value(), self.data_port.value(), self.control_port.value()
        if self.deployment is not None:
            if self.deployment.stream is not None or (self.deployment.process is not None and self.deployment.process.poll() is None):
                self.set_state('Unknown', 'The earlier loader stream is still connected. Refresh and authenticate that receiver before starting another; closing the stream is not a safe receiver stop.')
                return
            self.deployment.close()  # Reap an ended local stream; never use this as a remote stop.
        self.deployment = Deployment()
        deployment = self.deployment
        self.set_state('Starting', 'Deploying to the ELF loader. Waiting for encrypted startup credentials and an authenticated receiver response…')
        self._work('start', lambda progress: deployment.start(host, sdk, elf, *ports, self.cancellation, progress=progress))

    @Slot()
    def pair(self) -> None:
        if self.busy() or self.transfer_active:
            return
        host, code, port = self.host.text().strip(), self.code.text(), self.control_port.value()
        self.set_state('Unknown', 'Pairing securely with the receiver…')
        self._work('pair', lambda progress: pair_receiver(host, code, port, self.cancellation))

    @Slot()
    def stop_receiver(self) -> None:
        if self.busy() or self.transfer_active or not self.ready or self.paired is None:
            return
        paired = self.paired
        self.set_state('Stopping', 'Requesting authenticated shutdown of this idle receiver…')
        self._work('stop', lambda progress: stop_receiver(paired, self.cancellation))

    @Slot()
    def refresh(self) -> None:
        if self.busy() or self.transfer_active:
            return
        host, port, paired = self.host.text().strip(), self.control_port.value(), self.paired
        if paired is not None:
            self._work('status', lambda progress: receiver_status(paired, self.cancellation))
        else:
            self._work('identify', lambda progress: identify(host, port, self.cancellation))

    @Slot()
    def poll(self) -> None:
        if self.paired is not None:
            self.refresh()

    @Slot()
    def cancel(self) -> None:
        if self.busy() and self.operation != 'stop':
            self.cancellation.cancel()
            self.cancel_button.setEnabled(False)
            self.error.setText('Cancelling the local wait. This does not prove the remote ELF stopped.')

    @Slot(object)
    def _result(self, result: object) -> None:
        if self.operation == 'export' and isinstance(result, Path):
            self.set_state(self.state, f'Receiver ELF exported to {result}')
        elif isinstance(result, PairedReceiver):
            if self.operation == 'start':
                self._owned_receiver = result
            self.install(result)
        elif isinstance(result, ReceiverInfo):
            if self.operation == 'status' and self.paired is not None:
                self.install(replace(self.paired, info=result))
            else:
                self.info = result
                self.version.setText(f'Version: {result.version} (unverified) • Listening port: {result.port} • Control port: {result.control_port}')
                self.advanced.set_expanded(True)
                self.set_state('Unknown', 'Receiver found. Enter its six-digit PS5 code to authenticate before sending or stopping.')
        elif self.operation == 'stop':
            stopped = self.paired
            owns_stopped = (stopped is not None and self._owned_receiver is not None
                            and stopped.host == self._owned_receiver.host
                            and stopped.info.session_id == self._owned_receiver.info.session_id)
            self.paired = None
            self.code.clear()
            self.monitor.stop()
            if owns_stopped:
                if self.deployment is not None:
                    self.deployment.close()
                    self.deployment = None
                self._owned_receiver = None
            self.set_state('Stopped', 'Receiver acknowledged shutdown and both listeners closed. PS5 loader unloading behaviour depends on its implementation.')
        else:
            self.set_state('Error', 'Receiver operation returned no usable result.')

    @Slot(str)
    def _error(self, message: str) -> None:
        if self.operation == 'export':
            self.set_state(self.state, message)
            return
        self.monitor.stop()
        if 'Receiver session changed' in message:
            self.paired = None
            self.info = None
            self.code.clear()
            self.version.setText('Version: unknown • Listening port: unknown')
        # A refused authenticated probe detects termination, but does not prove
        # the payload returned/unloaded. Keep its credential for an explicit Refresh.
        if self.operation == 'stop' and 'busy' in message and self.paired is not None:
            self.info = replace(self.paired.info, busy=True)
            self.paired = replace(self.paired, info=self.info)
            self.set_state('Running', message)
            return
        uncertain_start = self.operation == 'start' and self.deployment is not None and self.deployment.attempted
        state = 'Unknown' if self.operation in ('stop', 'status') or uncertain_start else 'Error'
        self.set_state(state, message)

    @Slot(str)
    def _cancelled(self, message: str) -> None:
        if self.operation == 'export':
            self.set_state(self.state, 'Receiver ELF export cancelled.')
            return
        self.monitor.stop()
        self.set_state('Unknown', 'Local operation cancelled. The ELF may still be running. Refresh or pair before starting again.')

    @Slot()
    def _finished(self) -> None:
        self.worker = None
        self.task = None
        self.operation = ''
        self.update_controls()
        self.credentials_changed.emit()

    def shutdown_local(self) -> None:
        self.monitor.stop()
        if self.deployment is not None:
            self.deployment.close()
            self.deployment = None
        self.paired = None
        self._owned_receiver = None
        self.code.clear()

    @Slot()
    def choose_sdk(self) -> None:
        path = QFileDialog.getExistingDirectory(self, 'Choose PS5 Payload SDK root', self.sdk.text())
        if path:
            self.sdk.setText(path)

    @Slot()
    def choose_elf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Choose receiver ELF built from this branch', self.elf.text(), 'ELF payload (*.elf)')
        if path:
            self.elf.setText(path)

    @Slot()
    def export_elf(self) -> None:
        if self.busy() or self.transfer_active:
            return
        destination, _ = QFileDialog.getSaveFileName(self, 'Export PS5 Receiver ELF',
            'minecraft_playden_receiver.elf', 'ELF payload (*.elf)')
        if destination:
            sdk, elf = Path(self.sdk.text()).expanduser(), Path(self.elf.text()).expanduser()
            self._work('export', lambda progress: export_receiver_elf(sdk, elf, Path(destination), self.cancellation))
