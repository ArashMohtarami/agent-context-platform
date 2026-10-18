"""Copy required operator tools/libs/notices from a pinned matching Debian image.

No apt mirror, build-time package resolution, or development tools in the final image.
Only fixed trusted image executables are inspected with ldd (never user input).
"""

import re
import shutil
import subprocess
from pathlib import Path

root = Path("/operator-runtime")
files = [Path("/usr/bin/git"), *Path("/usr/lib/git-core").rglob("*")]
files.extend(Path("/usr/lib").glob("*-linux-gnu/libgssapi_krb5.so.2"))
libraries = set()
for path in files:
    if not path.is_file():
        continue
    with path.open("rb") as handle:
        if handle.read(4) != b"\x7fELF":
            continue
    result = subprocess.run(["ldd", str(path)], check=True, text=True, capture_output=True)  # noqa: S603,S607
    libraries.update(Path(value) for value in re.findall(r"(/[^\s()]+)", result.stdout))
for path in [Path("/usr/bin/git"), *libraries]:
    target = root / (path.parent.resolve() / path.name).relative_to("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
for path in (
    "/usr/lib/git-core",
    "/usr/share/git-core",
    "/etc/ssl/certs",
    "/usr/share/common-licenses",
):
    shutil.copytree(path, root / path.lstrip("/"), symlinks=False, dirs_exist_ok=True)
# Preserve every involved Debian package's copyright and redistributed notices.
for path in libraries | {Path("/usr/bin/git")}:
    result = subprocess.run(  # noqa: S603 - fixed trusted image input.
        ["/usr/bin/dpkg-query", "-S", str(path.resolve())], text=True, capture_output=True
    )
    for line in result.stdout.splitlines():
        package = line.split(": ", 1)[0].split(":", 1)[0]
        docs = Path("/usr/share/doc") / package
        if docs.is_dir():
            shutil.copytree(docs, root / docs.relative_to("/"), symlinks=False, dirs_exist_ok=True)
