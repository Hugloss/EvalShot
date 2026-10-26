"""Independent grader health: syntax plus known broken-fixture rejection."""
import pathlib
import subprocess
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent.parent
case = sys.argv[1]
if case not in {"ranges", "deploy-order", "cidr-policy"}:
    raise SystemExit("unknown frozen oracle case")
verifier = root / "oracles" / ("verify_" + case.replace("-", "_") + ".py")
compile(verifier.read_bytes(), str(verifier), "exec")
with tempfile.TemporaryDirectory(prefix="evalshot-oracle-health-") as temporary:
    candidate = pathlib.Path(temporary)
    for source in (root / "fixtures" / case).rglob("*"):
        if source.is_file():
            target = candidate / source.relative_to(root / "fixtures" / case)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    result = subprocess.run([sys.executable, "-I", "-B", str(verifier), str(candidate)],
        capture_output=True, timeout=30)
    if result.returncode != 1 or b"FAILED" not in result.stderr:
        raise SystemExit("frozen independent grader did not reject its known broken fixture")
print("frozen-oracle-health-passed")
