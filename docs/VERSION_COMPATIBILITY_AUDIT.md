# Metadata compatibility audit

Baseline: `ca8539b` on `feat/ps5-network-transfer`.

## Existing architecture

`discovery` and the read-only `importers` adapters identify local clients.
`ClientSource` verifies the client against official metadata and preserves instance
content. `VersionResolver` obtains Mojang metadata; `profiles`, `fabric`,
`dependencies` and `arguments` turn it into a Windows x64 launch plan.
`exporter` stages and publishes atomically only after `validator` verifies the
inventory, client/dependency JARs, native extraction and regenerated arguments.
The GUI and CLI share that engine; USB/LAN consume its existing package layout.

## Findings before implementation

| Area | Restriction or gap | Assessment |
| --- | --- | --- |
| Version selection | Resolver requires standalone structured arguments and Java 17+ | Structured/legacy argument adapters and bounded metadata inheritance can be reusable; arbitrary patched clients still need a separate adapter |
| Official metadata | Manifest always refreshed, even with a populated cache | Unnecessary network dependency; known versions should use verified cached metadata |
| Java | Always fetches latest Temurin Windows JDK; no permission, local runtime selection or target validation | Replace with exact metadata-major selection, local/cache preference, explicit download permission and Windows x64 checks; never execute host Java |
| Java 8 | Dedicated same-JVM bootstrap / manifest classpath | Adapter implemented; Java 8 runtime retained. Vanilla 1.16 and prepared Fabric 1.14.4/1.16 are physically confirmed; see [launch evidence and limits](JAVA8-LAUNCH.md) |
| Libraries | Mojang rules already target Windows, independent of host; old classifier maps and new native artifacts supported | Preserve this; validate malformed rules and avoid classpath duplicates; installed verified Windows artifacts can seed the downloader |
| Native extraction | Flattens DLLs and detects conflicts, validates bytes against archives | Preserve this; Linux/macOS libraries must never replace Windows natives |
| Assets | Content-addressed object downloads; virtual/resource maps explicitly rejected | Mapping objects to metadata-selected portable paths is reusable; require safe paths and validate mapped content |
| Arguments | Metadata placeholders and rules supported; ignores `default-user-jvm` | Include current official JVM defaults and retain offline identities, portable paths and explicit failure on unknown placeholders |
| Logging | Vanilla logging metadata ignored; only Prism/Fabric logging copied | Resolve and validate official logging for both Vanilla and Fabric |
| Fabric | Installed official inheritance and Prism component layers, exact loader version and mandatory dependency checks already exist | Retain these adapters and their safeguards; propagate supported Java requirements instead of hardcoding loader releases |
| Launcher differences | Official uses `versions/<id>`, inherited profiles and shared libraries; Prism/MultiMC use `mmc-pack.json`, ordered cached components, patches and shared Maven client | Retain explicit adapters; reuse installed official metadata only when its digest matches the manifest; never read account databases |
| Compatibility evidence | Registry contains only exact PS5 success reports; unlisted versions may build | Correct separation already exists; add an explicit metadata capability result without inferring console evidence from tests |
| Reference profile | `reference-26.3` pins a known diagnostic dependency set | Intentional opt-in reproduction, not the default engine or a general version allowlist |

## Implementation and evidence

See [compatibility](COMPATIBILITY.md) for the resulting support matrix and remaining
technical blockers, and `tests/test_version_matrix.py` for metadata-based packaging
coverage. Metadata snapshots record upstream URLs and hashes under
`tests/fixtures/versions/`; binary/game payloads in tests are synthetic. No physical
console success is inferred from these checks. Release approval and CI policies
are outside this change; no desktop packaging or release workflow is invoked.

## Verification of the implementation

- Full source suite: 763 passed, 1 skipped, 343 subtests passed.
- Receiver lifecycle/pairing, GUI handoff and host network tests with address and
  undefined-behavior sanitizers: 197 passed.
- Linux, Windows and macOS mypy targets: all passed, 60 source files per target.
- Public-source audit: passed, 257 tracked/new source files checked.
- The matrix covers eight versions, Vanilla/Fabric and three launcher layouts:
  48 configurations, each with a validated initial export and offline rebuild.
- Existing package manifests and Playden launch paths are preserved. The new
  argument-policy marker is inside `metadata/runtime.json`, which avoids changing
  the receiver's strict top-level manifest contract. A regression verifies that
  older packages still use their original argument and logging validation policy.

Remaining conservative checks include known critical Java dependency entry points
(such as Fastutil for the modern Vanilla main class), restricted installed custom
native profiles, and explicit refusal of unknown launch placeholders. A future
upstream format or dependency-generation change may need an adapter/probe update;
adding a version that uses an understood format requires metadata rather than a
new per-version packaging implementation. Runtime PE/major checks establish target
structure, not Windows ABI, graphics-driver or Playden runtime acceptance.
