"""Version-checked, backed-up LLBot patch. Does not restart any service."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


SUPPORTED_VERSION = "7.12.15"
MARKER = "// QQBOT_FORWARD_SEND_TIMEOUT_60S"
ORIGINAL = """\tasync sendMsg(peer, msgElements) {
\t\tlet totalSize = 0;
\t\tfor (const fileElement of msgElements) if (fileElement.elementType === ElementType.Ptt) totalSize += +fileElement.pttElement.fileSize;
\t\telse if (fileElement.elementType === ElementType.File) totalSize += +fileElement.fileElement.fileSize;
\t\telse if (fileElement.elementType === ElementType.Video) totalSize += +fileElement.videoElement.fileSize;
\t\telse if (fileElement.elementType === ElementType.Pic) totalSize += +fileElement.picElement.fileSize;
\t\tconst timeout = 1e4 + totalSize / 1024 / 256 * 1e3;
\t\tconst uniqueId = await this.generateMsgUniqueId(peer.chatType);"""
TIMEOUT_LINE = "\t\tconst timeout = 1e4 + totalSize / 1024 / 256 * 1e3;"
TIMEOUT_PATCH = """\t\t// QQBOT_FORWARD_SEND_TIMEOUT_60S
\t\tconst hasForwardCard = msgElements.some((element) => {
\t\t\tif (element.elementType !== 10) return false;
\t\t\ttry {
\t\t\t\treturn JSON.parse(element.arkElement?.bytesData)?.app === "com.tencent.multimsg";
\t\t\t} catch {
\t\t\t\treturn false;
\t\t\t}
\t\t});
\t\tconst timeout = Math.max(1e4 + totalSize / 1024 / 256 * 1e3, hasForwardCard ? 6e4 : 0);"""
PATCHED = ORIGINAL.replace(TIMEOUT_LINE, TIMEOUT_PATCH)


def patched_source(source: str) -> str:
    if source.count(PATCHED) == 1 and source.count(MARKER) == 1 and ORIGINAL not in source:
        return source
    if MARKER in source or source.count(ORIGINAL) != 1:
        raise ValueError("LLBot source differs from the verified layout; refusing to patch")
    return source.replace(ORIGINAL, PATCHED, 1)


def patch_file(target: Path, node: Path, *, apply: bool = False) -> str:
    if target.is_symlink():
        raise ValueError("Refusing to patch a symbolic link")
    target = target.resolve(strict=True)
    if target.name != "llbot.js":
        raise ValueError("Target must be llbot.js")
    package = json.loads(target.with_name("package.json").read_text(encoding="utf-8"))
    if package.get("version") != SUPPORTED_VERSION:
        raise ValueError(f"Only LLBot {SUPPORTED_VERSION} is verified; review upgraded code first")
    original = target.read_bytes()
    result = patched_source(original.decode("utf-8")).encode("utf-8")
    if result == original:
        return "already patched"
    descriptor, name = tempfile.mkstemp(prefix=".qqbot-timeout-", suffix=".mjs", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(result)
            output.flush()
            os.fsync(output.fileno())
        subprocess.run([str(node), "--check", str(temporary)], check=True, capture_output=True, timeout=30)
        if not apply:
            return "ready: syntax checked, target unchanged"
        if target.read_bytes() != original:
            raise ValueError("LLBot changed during validation; refusing to overwrite")
        digest = hashlib.sha256(original).hexdigest()
        backup = target.with_name(f"llbot.js.before-forward-timeout-{digest[:16]}.bak")
        try:
            with backup.open("xb") as output:
                output.write(original)
                output.flush()
                os.fsync(output.fileno())
            shutil.copystat(target, backup)
        except FileExistsError:
            if backup.read_bytes() != original:
                raise ValueError("Existing backup differs; refusing to overwrite") from None
        shutil.copystat(target, temporary)
        if hasattr(os, "chown") and os.geteuid() == 0:
            info = target.stat()
            os.chown(temporary, info.st_uid, info.st_gid)
        os.replace(temporary, target)
        return f"patched: forward card timeout >= 60s; backup={backup}; original_sha256={digest}"
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("/opt/llbot/bin/llbot/llbot.js"))
    parser.add_argument("--node", type=Path, default=Path("/opt/llbot/bin/llbot/node"))
    parser.add_argument("--apply", action="store_true", help="Apply after syntax check; otherwise check only")
    args = parser.parse_args()
    print(patch_file(args.target, args.node, apply=args.apply))


if __name__ == "__main__":
    main()
