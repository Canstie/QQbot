import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from tools import patch_llbot_forward_timeout as patch


def test_patch_changes_only_verified_timeout_block_and_is_idempotent():
    source = "before\n" + patch.ORIGINAL + "\nafter"
    result = patch.patched_source(source)
    assert result == "before\n" + patch.PATCHED + "\nafter"
    assert patch.patched_source(result) == result


@pytest.mark.parametrize("source", ["unknown", patch.ORIGINAL * 2, patch.MARKER,
                                          patch.PATCHED.replace("6e4", "3e4")])
def test_changed_source_is_rejected(source):
    with pytest.raises(ValueError):
        patch.patched_source(source)


@pytest.fixture
def bundle(tmp_path):
    target = tmp_path / "llbot.js"
    target.write_text("class Sender {\n" + patch.ORIGINAL + "\n}\n}\n", encoding="utf-8", newline="\n")
    (tmp_path / "package.json").write_text(json.dumps({"version": "7.12.15"}))
    return target


def test_apply_backup_and_idempotence(bundle, monkeypatch):
    syntax = Mock()
    monkeypatch.setattr(patch.subprocess, "run", syntax)
    original = bundle.read_bytes()
    assert patch.patch_file(bundle, Path("node")) == "ready: syntax checked, target unchanged"
    assert bundle.read_bytes() == original
    assert not list(bundle.parent.glob("*.bak"))
    assert patch.patch_file(bundle, Path("node"), apply=True).startswith("patched:")
    backups = list(bundle.parent.glob("*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert patch.MARKER in bundle.read_text()
    assert patch.patch_file(bundle, Path("node"), apply=True) == "already patched"
    assert len(list(bundle.parent.glob("*.bak"))) == 1
    assert not list(bundle.parent.glob(".qqbot-timeout-*"))
    assert syntax.call_count == 2


def test_syntax_failure_keeps_original(bundle, monkeypatch):
    original = bundle.read_bytes()
    monkeypatch.setattr(patch.subprocess, "run", Mock(side_effect=subprocess.CalledProcessError(1, "node")))
    with pytest.raises(subprocess.CalledProcessError):
        patch.patch_file(bundle, Path("node"), apply=True)
    assert bundle.read_bytes() == original
    assert not list(bundle.parent.glob("*.bak"))
    assert not list(bundle.parent.glob(".qqbot-timeout-*"))


def test_upgraded_version_is_rejected(bundle):
    original = bundle.read_bytes()
    bundle.with_name("package.json").write_text('{"version":"8.0.0"}')
    with pytest.raises(ValueError, match="Only LLBot"):
        patch.patch_file(bundle, Path("node"), apply=True)
    assert bundle.read_bytes() == original


def test_concurrent_change_is_not_overwritten(bundle, monkeypatch):
    monkeypatch.setattr(patch.subprocess, "run", lambda *a, **kw: bundle.write_bytes(b"new version"))
    with pytest.raises(ValueError, match="changed during validation"):
        patch.patch_file(bundle, Path("node"), apply=True)
    assert bundle.read_bytes() == b"new version"
    assert not list(bundle.parent.glob("*.bak"))


def test_failed_atomic_replace_keeps_original_and_backup(bundle, monkeypatch):
    original = bundle.read_bytes()
    monkeypatch.setattr(patch.subprocess, "run", Mock())
    monkeypatch.setattr(patch.os, "replace", Mock(side_effect=OSError("read-only")))
    with pytest.raises(OSError):
        patch.patch_file(bundle, Path("node"), apply=True)
    assert bundle.read_bytes() == original
    assert next(bundle.parent.glob("*.bak")).read_bytes() == original
    assert not list(bundle.parent.glob(".qqbot-timeout-*"))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required for JS execution")
def test_actual_javascript_preserves_other_messages_and_large_attachment_timeouts(bundle):
    node = Path(shutil.which("node"))
    assert patch.patch_file(bundle, node).startswith("ready:")
    script = """
const ElementType = {Ptt:4, File:3, Video:5, Pic:2};
class Sender {
    async generateMsgUniqueId() { return 'test'; }
""" + patch.PATCHED + "\nreturn timeout;\n}\n}\n" + """
(async () => {
    const forward = {elementType:10, arkElement:{bytesData:JSON.stringify({app:'com.tencent.multimsg'})}};
    const photo = {elementType:2, picElement:{fileSize:1048576}};
    const large = {elementType:3, fileElement:{fileSize:52428800}};
    const cases = [[], [forward], [photo], [large], [forward, large],
        [{elementType:10, arkElement:{bytesData:'broken'}}],
        [{elementType:10, arkElement:{bytesData:'{"app":"other"}'}}],
        [{elementType:10}], [{elementType:10, arkElement:{bytesData:'null'}}]];
    const result = [];
    for (const elements of cases) result.push(await new Sender().sendMsg({chatType:2}, elements));
    process.stdout.write(JSON.stringify(result));
})();
"""
    result = subprocess.run([str(node), "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [10000, 60000, 14000, 210000, 210000, 10000, 10000, 10000, 10000]


NATIVE_FIXTURE = (patch.NATIVE_HEAD + "\n\t\t" + patch.NATIVE_RETURN + """
            msgIds, srcPeer, destPeer
        ], {
            resultCb: (payload) => {
                for (const msgRecord of payload) if (CONDITION) return true;
                return false;
            }
        })).find((msgRecord) => {
            if (CONDITION) return true;
            return false;
        });
\t}""".replace("CONDITION", patch.NATIVE_CONDITION) + patch.NATIVE_TAIL + " return null; }")


def test_native_forward_patch_is_scoped_and_idempotent():
    source = "before\n" + NATIVE_FIXTURE + "\nafter"
    result = patch.native_patched_source(source)
    assert result.startswith("before\n") and result.endswith("\nafter")
    assert result.count(patch.NATIVE_CONFIRMED) == 2
    assert patch.NATIVE_END in result
    assert patch.native_patched_source(result) == result


@pytest.mark.parametrize("source", ["unknown", NATIVE_FIXTURE * 2,
                                          NATIVE_FIXTURE.replace("return false", "return sendStatus")])
def test_native_forward_patch_rejects_changed_layout(source):
    with pytest.raises(ValueError):
        patch.native_patched_source(source)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required for JS execution")
def test_native_forward_waits_for_final_status_and_rejects_failure():
    script = "const selfInfo={uid:'bot'}; class Sender {\n" + patch.native_patched_source(NATIVE_FIXTURE) + "\n}\n" + """
(async()=>{
    const outcomes=[];
    for(const status of [2,3]) {
        const sender=new Sender();
        const message={msgType:11,subMsgType:7,peerUid:'123',senderUid:'bot',sendStatus:status};
        sender.ctx={pmhq:{invoke:async (name,args,options)=>{
            if(options.resultCb([{...message,sendStatus:1}])) throw new Error('accepted pending status');
            if(!options.resultCb([message])) throw new Error('ignored terminal status');
            return [message];
        }}};
        try { const result=await sender.multiForwardMsg({}, {peerUid:'123'}, ['1']); outcomes.push(result.sendStatus); }
        catch(error) { if(!error.message.includes('sendStatus=3')) throw error; outcomes.push('failed'); }
    }
    process.stdout.write(JSON.stringify(outcomes));
})();
"""
    result = subprocess.run([shutil.which("node"), "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [2, "failed"]
