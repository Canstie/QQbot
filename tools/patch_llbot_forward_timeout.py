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
NATIVE_MARKER = "// QQBOT_NATIVE_FORWARD_CONFIRMED_RESULT"
NATIVE_HEAD = "\tasync multiForwardMsg(srcPeer, destPeer, msgIds) {"
NATIVE_TAIL = "\n\tasync getSingleMsg(peer, msgSeq) {"
NATIVE_RETURN = 'return (await this.ctx.pmhq.invoke("nodeIKernelMsgService/multiForwardMsgWithComment", ['
NATIVE_RESULT = 'const result = (await this.ctx.pmhq.invoke("nodeIKernelMsgService/multiForwardMsgWithComment", ['
NATIVE_CONDITION = "msgRecord.msgType === 11 && msgRecord.subMsgType === 7 && msgRecord.peerUid === destPeer.peerUid && msgRecord.senderUid === selfInfo.uid"
NATIVE_CONFIRMED = "(msgRecord.sendStatus === 2 || msgRecord.sendStatus === 3) && " + NATIVE_CONDITION
NATIVE_END = """\t\t// QQBOT_NATIVE_FORWARD_CONFIRMED_RESULT
\t\tif (!result || result.sendStatus !== 2) throw new Error("QQ native forward failed: sendStatus=" + result?.sendStatus);
\t\treturn result;
\t}"""


def native_patched_source(source: str) -> str:
    if source.count(NATIVE_HEAD) != 1 or source.count(NATIVE_TAIL) != 1:
        raise ValueError("Native forward method layout changed; refusing to patch")
    start = source.index(NATIVE_HEAD)
    end = source.index(NATIVE_TAIL, start)
    block = source[start:end]
    if NATIVE_MARKER in source:
        if (source.count(NATIVE_MARKER) == 1 and block.count(NATIVE_CONFIRMED) == 2
                and block.count(NATIVE_RESULT) == 1 and block.endswith(NATIVE_END)):
            return source
        raise ValueError("Native forward patch was modified; refusing to overwrite")
    if (block.count(NATIVE_RETURN) != 1 or block.count(NATIVE_CONDITION) != 2
            or "sendStatus" in block or not block.endswith("\n\t}")):
        raise ValueError("Native forward implementation changed; refusing to patch")
    patched = block.replace(NATIVE_RETURN, NATIVE_RESULT, 1).replace(NATIVE_CONDITION, NATIVE_CONFIRMED)
    patched = patched[:-len("\t}")] + NATIVE_END
    return source[:start] + patched + source[end:]


def patched_source(source: str) -> str:
    if source.count(PATCHED) == 1 and source.count(MARKER) == 1 and ORIGINAL not in source:
        return source
    if MARKER in source or source.count(ORIGINAL) != 1:
        raise ValueError("LLBot source differs from the verified layout; refusing to patch")
    return source.replace(ORIGINAL, PATCHED, 1)


def patch_file(target: Path, node: Path, *, apply: bool = False, native_result: bool = False) -> str:
    if target.is_symlink():
        raise ValueError("Refusing to patch a symbolic link")
    target = target.resolve(strict=True)
    if target.name != "llbot.js":
        raise ValueError("Target must be llbot.js")
    package = json.loads(target.with_name("package.json").read_text(encoding="utf-8"))
    if package.get("version") != SUPPORTED_VERSION:
        raise ValueError(f"Only LLBot {SUPPORTED_VERSION} is verified; review upgraded code first")
    original = target.read_bytes()
    updated = patched_source(original.decode("utf-8"))
    if native_result:
        updated = native_patched_source(updated)
    result = updated.encode("utf-8")
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
        return f"patched: forward card timeout >= 60s; native_result={native_result}; backup={backup}; original_sha256={digest}"
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("/opt/llbot/bin/llbot/llbot.js"))
    parser.add_argument("--node", type=Path, default=Path("/opt/llbot/bin/llbot/node"))
    parser.add_argument("--apply", action="store_true", help="Apply after syntax check; otherwise check only")
    parser.add_argument("--native-result", action="store_true", help="Also wait for native forward success/failure, rejecting sendStatus=3")
    args = parser.parse_args()
    print(patch_file(args.target, args.node, apply=args.apply, native_result=args.native_result))


if __name__ == "__main__":
    main()
