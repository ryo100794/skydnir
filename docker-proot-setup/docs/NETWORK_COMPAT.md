# pdocker network visibility and port truth

Snapshot date: 2026-05-15.

pdockerd still executes containers in Android's app/user-space networking
context. It does not yet create a kernel network namespace or a real bridge
interface. The current work makes container network identity visible through
Docker-compatible API fields, records requested publish mappings, and separately
reports whether a mapping has active listener/proxy/rewrite evidence.

## Docker-compatible surface

For each created container, pdockerd now stores:

- `NetworkSettings.IPAddress`
- `NetworkSettings.Networks.bridge.IPAddress`
- `NetworkSettings.Networks.<name>.NetworkID`
- `NetworkSettings.Networks.<name>.EndpointID`
- `NetworkSettings.Networks.<name>.Aliases`
- `NetworkSettings.Ports`
- `/containers/json[].Ports`
- `/networks`, `/networks/{name}`, `/networks/{name}/connect`, and
  `/networks/{name}/disconnect` metadata

The IP address fields are stable synthetic identities in `10.88.0.0/16`,
derived from the container ID. They are intended for UI/API identity and future
hook lookup; they are not assigned by Android's kernel, are not bridge
addresses, and must not be treated as reachability proof.

Example inspect shape:

```json
{
  "NetworkSettings": {
    "IPAddress": "10.88.12.34",
    "Ports": {
      "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}],
      "443/tcp": null
    },
    "Networks": {
      "bridge": {
        "NetworkID": "5c33...",
        "EndpointID": "7db1...",
        "IPAddress": "10.88.12.34",
        "Aliases": ["web-1", "web"]
      }
    }
  }
}
```

Network IDs and endpoint IDs are stable hashes, not kernel object IDs. Compose
project/service aliases and endpoint aliases are preserved for inspect/list
responses and for the current `/etc/hosts` compatibility injection.

## pdocker extension surface

`/containers/{id}/json` also includes `PdockerNetwork`:

```json
{
  "PdockerNetwork": {
    "IPAddress": "10.88.12.34",
    "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}]},
    "PortRewrite": [
      {
        "ContainerPort": 80,
        "Protocol": "tcp",
        "HostIp": "127.0.0.1",
        "HostPort": 18080,
        "Hook": "bind",
        "Status": "planned"
      }
    ],
    "Kind": "host-network-with-syscall-hook-plan",
    "Runtime": "host-network-only",
    "Limitation": "pdocker records Docker/Compose network metadata, but Android runtime is host-network-only: there is no TUN, bridge namespace, iptables, or embedded DNS yet. Named-network aliases are /etc/hosts compatibility entries and all process traffic still uses the Android app network.",
    "Warnings": [
      "pdocker records Docker/Compose network metadata, but Android runtime is host-network-only: there is no TUN, bridge namespace, iptables, or embedded DNS yet. Named-network aliases are /etc/hosts compatibility entries and all process traffic still uses the Android app network.",
      "pdocker records requested port publishing, but Android sandbox runtime is still host-network-only; bind/connect syscall rewrite is planned and not active yet."
    ]
  },
  "PdockerWarnings": [
    "pdocker records requested port publishing, but Android sandbox runtime is still host-network-only; bind/connect syscall rewrite is planned and not active yet."
  ]
}
```

`PortRewrite` is deliberately explicit. Runtime hook/proxy code can use it to
rewrite container `bind(2)` / related socket calls from a synthetic container
address/port to the Android-host-visible port. The entry's requested
`Status`/legacy booleans are not proof that traffic is flowing.

`PdockerNetwork.PortMappingStatus` is the truth surface for published ports:

- `planned`: a stopped/created container has a requested mapping.
- `inactive`: the container is running, but no pdocker-owned listener, proxy,
  or rewrite evidence exists for the mapping.
- `active`: pdockerd verified a live container-owned listener from `/proc/net`
  or accepted explicit live runtime/proxy/rewrite evidence.
- `conflict`: another container claims the same host port, or `/proc/net` shows
  a matching host listener not owned by this container. Wildcard host binds
  (`0.0.0.0`, `::`, empty) conflict with specific addresses for the same
  protocol and port.

## Port source rules

- `Config.ExposedPorts` and image `config.ExposedPorts` are merged.
- `HostConfig.PortBindings` is honored when present.
- `HostConfig.PublishAllPorts=true` allocates deterministic host ports.
- Compose `NetworkingConfig.EndpointsConfig.<network>.Aliases` and
  `com.docker.compose.service` labels are reflected in endpoint aliases.
- Invalid or empty host ports fall back to deterministic ports derived from the
  container ID and container port.
- Container create responses and inspect payloads expose warnings whenever port
  publishing or named-network metadata is requested. Those warnings mean
  Docker-compatible metadata was recorded, but the Android runtime remains
  host-network-only. Use `PdockerNetwork.PortMappingStatus`, not
  `NetworkSettings.Ports`, to decide whether traffic is actually backed by a
  listener/proxy/rewrite.

This keeps `docker ps`, `docker inspect`, UI widgets, and future syscall hook
logic reading the same persisted state.

## Test coverage

`scripts/verify_all.sh` includes a reusable regression named `pdocker network
identity + port plan`. `scripts/verify_runtime_contract.py` also imports
`bin/pdockerd` directly and asserts stable network IDs, endpoint IDs,
Compose/service aliases, `/etc/hosts` peer alias injection, Docker-style
`Ports`, synthetic identity display, disconnect cleanup, host-network-only
warnings, active evidence handling, legacy metadata fallback, and host-port
conflict behavior. `tests/test_network_port_mapping_status.py` covers the
conflict corpus and verifies that metadata-only mappings never become active.
