ifndef PS5_PAYLOAD_SDK
$(error Set PS5_PAYLOAD_SDK to the installed ps5-payload-sdk directory)
endif
include $(PS5_PAYLOAD_SDK)/toolchain/prospero.mk

ELF := minecraft_playden_receiver.elf
DIAGNOSTICS ?= 0
SOCKET_BUFFER ?= 0
BUFFERED_READS ?= 1
CFLAGS += -DRECEIVER_BUFFERED_READS=$(BUFFERED_READS)
CFLAGS += -DRECEIVER_DIAGNOSTICS=$(DIAGNOSTICS) -DRECEIVER_SOCKET_BUFFER=$(SOCKET_BUFFER)
CFLAGS += -std=c11 -DPS5_RECEIVER -Wall -Wextra -Werror -O2

.PHONY: all clean
all: $(ELF)

$(ELF): main.c wire.h paths.h package.h sdk_fs.h diagnostics.h management.h pairing_crypto.h $(wildcard vendor/bearssl/src/int/*) $(wildcard vendor/bearssl/src/*.h) $(wildcard vendor/bearssl/inc/*.h) vendor/sha-256.c vendor/sha-256.h ../diagnostic/recipe_check.h
	$(CC) $(CFLAGS) -o $@ main.c vendor/sha-256.c

clean:
	rm -f $(ELF)

# No build target contacts a PS5. The GUI deploys only on an explicit Start click.
