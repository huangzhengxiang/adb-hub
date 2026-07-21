# SCP model → phone → run → retrieve example

This is one complete agent example for the ADB Hub session API.  It fetches a
runner, model payload, prompt, and device launcher from the remote client over
SCP; pushes them to the leased phone workdir; runs the launcher through ADB
Hub; and downloads the device output back to the remote client.

The checked-in `bin/model_runner.sh` is a portable smoke runner so the example
can run on an ordinary Android shell.  It reads the model payload and prompt
and writes `outputs/model_output.txt`.  For a real native test, replace it
with the Android executable, add any shared objects under `lib/`, and replace
`model/demo_model.txt` with the real model while keeping the same relative
paths.

## Prerequisites

Run this command from the repository root on the **remote client** that owns
these files.  The adb-hub server must be running and connected to one online
phone.  Its `.env` (or the runner process environment) must provide
`ADB_HUB_AUTH_SECRET`, `ADB_HUB_PUBLIC_HOST`/`ADB_HUB_URL`, and the SCP values
`ADB_HUB_SCP_HOST`, `ADB_HUB_SCP_PORT`, `ADB_HUB_SCP_USER`, and
`ADB_HUB_SCP_PASSWORD`.  Credentials are deliberately not present in this
plan or directory.

```bash
python client/agent_session_runner.py \
  --plan client/examples/scp_model_phone_run/adb_hub_plan.json \
  --output client/examples/scp_model_phone_run/received/adb_hub_report.json
```

The runner resolves every `fetch.src` relative to this plan, then tells the
adb-hub server to SCP those files into its per-session host workdir.  It closes
the session even after a failed step, so the phone lease and temporary files do
not remain allocated.

After success, inspect these remote-client files:

- `received/model_output.txt` — program output retrieved from the phone.
- `received/run_stdout.txt` and `received/run_stderr.txt` — device launcher
  diagnostics.
- `received/adb_hub_report.json` — every SCP, ADB, and download step.

## Device launcher

The shell action in the plan is intentionally small:

```sh
mkdir -p outputs && sh ./run_on_device.sh > outputs/run_stdout.txt 2> outputs/run_stderr.txt
```

`run_on_device.sh` runs from the ADB Hub device session workdir.  It exports
`LD_LIBRARY_PATH` with the session `lib/` directory first, plus `PATH`,
`TMPDIR`, `HOME`, and `XDG_CACHE_HOME`, then executes the runner.  A native
deployment can select different staged paths without editing the script:

```sh
ADB_HUB_RUNNER=bin/my_native_runner \
ADB_HUB_MODEL=model/my_model.bin \
ADB_HUB_PROMPT=input/request.txt \
ADB_HUB_OUTPUT=outputs/result.json \
sh ./run_on_device.sh
```

To use those overrides in the plan, put the assignments before
`sh ./run_on_device.sh` in the `shell` action and add matching `fetch`/`push`
entries.  Keep output under `outputs/`, then add corresponding `pull` and
`download` actions.

## Offline verification

The verifier uses a fake ADB Hub client to exercise the real manifest runner
without SCP credentials, an ADB server, or a phone.  It checks the full API
operation order, report generation, and output-download paths:

```bash
python client/examples/scp_model_phone_run/verify_example.py
```

For a quick launcher-only smoke check on a POSIX host, copy this directory to a
temporary directory and run `ADB_HUB_SHELL=sh sh run_on_device.sh`; do not run
it in place if you want to keep generated `outputs/` out of the checkout.
