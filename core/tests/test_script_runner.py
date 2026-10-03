import json
import shutil
import sys

import pytest
from test_agent import PROVIDER, Script, call

from piyo.agent import Agent, ToolFinished
from piyo.models.turn import TurnDone
from piyo.safety import PermissionGate
from piyo.skills import SkillRegistry
from piyo.skills.manifest import load_skill_dir
from piyo.skills.runner import (
    MAX_OUTPUT_BYTES,
    ScriptError,
    ScriptRunner,
    env_name,
    secret_name,
)
from piyo.tools import Risk, ToolRegistry, core_tools
from piyo.tools.scripts import script_tools

HAVE_UV = shutil.which("uv") is not None
needs_uv = pytest.mark.skipif(not HAVE_UV, reason="uv is not installed")


def make_skill(root, name="demo", scripts=None, runtime="", secrets=()):
    folder = root / name
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    secret_line = f"  secrets: [{', '.join(secrets)}]\n" if secrets else ""
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo.\nrequires:\n  tools: [skill.run_script]\n{secret_line}{runtime}---\nBody\n",
        encoding="utf-8",
    )
    for file, code in (scripts or {}).items():
        (folder / "scripts" / file).write_text(code, encoding="utf-8")
    return load_skill_dir(folder, "user")


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    return tmp_path_factory.mktemp("work")  # shared so each skill's virtual environment is built once


def runner(work, secrets=None):
    return ScriptRunner(work, (secrets or {}).get)


ECHO = "import json, sys\nprint(json.dumps({'got': json.load(sys.stdin)}))\n"


@needs_uv
async def test_python_script_gets_json_in_and_out(tmp_path, work):
    skill = make_skill(tmp_path, scripts={"echo.py": ECHO})
    result = await runner(work).run(skill, "echo.py", {"city": "Pune", "n": 2})
    assert result.value == {"got": {"city": "Pune", "n": 2}}


@needs_uv
async def test_script_runs_in_a_scratch_folder_and_sees_only_declared_secrets(tmp_path, work):
    code = (
        "import json, os\n"
        "print(json.dumps({'cwd': os.getcwd(), 'keys': sorted(k for k in os.environ if k.startswith(('PIYO_', 'OPENAI', 'ANTHROPIC')))}))\n"
    )
    skill = make_skill(tmp_path, name="keyed", scripts={"env.py": code}, secrets=("API_KEY", "OTHER"))
    store = {secret_name("keyed", "API_KEY"): "s3cret-value", "OPENAI_API_KEY": "must-not-leak"}
    out = (await runner(work, store).run(skill, "env.py", {})).value
    assert out["keys"] == ["PIYO_SECRET_API_KEY"]  # declared and set; OTHER is not set; nothing else leaks
    assert "piyo-script-" in out["cwd"] and str(skill.path) not in out["cwd"]


@needs_uv
async def test_network_is_blocked_unless_declared(tmp_path, work):
    code = (
        "import json, socket\n"
        "try:\n    socket.create_connection(('example.com', 80), 1)\n    r = 'connected'\n"
        "except PermissionError as e:\n    r = 'blocked'\n"
        "except OSError:\n    r = 'oserror'\n"
        "print(json.dumps(r))\n"
    )
    blocked = make_skill(tmp_path, name="offline", scripts={"net.py": code})
    assert (await runner(work).run(blocked, "net.py", {})).value == "blocked"
    allowed = make_skill(
        tmp_path, name="online", scripts={"net.py": code}, runtime="runtime:\n  python:\n    network: true\n"
    )
    assert (await runner(work).run(allowed, "net.py", {})).value in ("connected", "oserror")  # not blocked by us


@needs_uv
async def test_timeout_kills_the_script(tmp_path, work):
    skill = make_skill(
        tmp_path, name="slow", scripts={"hang.py": "import time\ntime.sleep(60)\n"},
        runtime="runtime:\n  python:\n    timeout_s: 1\n",
    )
    with pytest.raises(ScriptError, match="longer than 1 seconds"):
        await runner(work).run(skill, "hang.py", {})


@needs_uv
async def test_runaway_output_is_stopped(tmp_path, work):
    code = "import sys\nwhile True:\n    sys.stdout.write('x' * 100000)\n"
    skill = make_skill(tmp_path, name="loud", scripts={"flood.py": code})
    with pytest.raises(ScriptError, match="printed more than"):
        await runner(work).run(skill, "flood.py", {})
    assert MAX_OUTPUT_BYTES == 1_000_000


@needs_uv
async def test_failures_and_bad_output_are_explained(tmp_path, work):
    skill = make_skill(
        tmp_path, name="bad",
        scripts={"boom.py": "raise RuntimeError('kaput')\n", "text.py": "print('hello')\n", "empty.py": ""},
    )
    with pytest.raises(ScriptError, match=r"failed \(exit 1\).*kaput"):
        await runner(work).run(skill, "boom.py", {})
    with pytest.raises(ScriptError, match="one JSON value"):
        await runner(work).run(skill, "text.py", {})
    with pytest.raises(ScriptError, match="one JSON value"):
        await runner(work).run(skill, "empty.py", {})


async def test_unknown_script_and_oversized_args(tmp_path, work):
    skill = make_skill(tmp_path, scripts={"echo.py": ECHO})
    with pytest.raises(ScriptError, match="no script 'nope.py'.*echo.py"):
        await runner(work).run(skill, "nope.py", {})
    with pytest.raises(ScriptError, match="no script"):
        await runner(work).run(skill, "../SKILL.md", {})  # only files listed under scripts/ can run
    with pytest.raises(ScriptError, match="too large"):
        await runner(work).run(skill, "echo.py", {"x": "y" * 200_000})


async def test_missing_runtimes_are_explained(tmp_path, work):
    skill = make_skill(tmp_path, name="two", scripts={"echo.py": ECHO, "go.ts": "console.log('1')"})
    no_tools = ScriptRunner(work, lambda n: None, uv="", deno="")
    no_tools._uv_bin = no_tools._deno_bin = None
    with pytest.raises(ScriptError, match="need uv"):
        await no_tools.run(skill, "echo.py", {})
    with pytest.raises(ScriptError, match="need Deno"):
        await no_tools.run(skill, "go.ts", {})


def test_deno_gets_exactly_the_declared_permissions(tmp_path, work):
    plain = make_skill(tmp_path, name="plain", scripts={"a.ts": "1"})
    r = runner(work)
    flags = r.deno_flags(plain, {}, work / "cache")
    assert flags[0] == "--no-prompt" and not any(f.startswith("--allow-net") for f in flags)
    assert any(f.startswith("--allow-read=") and str(plain.path) in f for f in flags)
    for banned in ("--allow-all", "-A", "--allow-write", "--allow-run", "--allow-env", "--allow-ffi", "--allow-sys"):
        assert banned not in flags
    net = r.deno_flags(plain, {"allow_net": ["api.example.com", "cdn.example.com"]}, work / "cache")
    assert "--allow-net=api.example.com,cdn.example.com" in net


@pytest.mark.skipif(not shutil.which("deno"), reason="deno is not installed")
async def test_deno_script_runs_and_cannot_use_the_network(tmp_path, work):
    code = (
        "const input = JSON.parse(new TextDecoder().decode(await Deno.readAll(Deno.stdin)));\n"
        "let net = 'ok';\ntry { await fetch('https://example.com'); } catch (e) { net = e.name; }\n"
        "console.log(JSON.stringify({input, net}));\n"
    )
    skill = make_skill(tmp_path, name="denoer", scripts={"go.ts": code})
    out = (await runner(work).run(skill, "go.ts", {"a": 1})).value
    assert out["input"] == {"a": 1} and out["net"] != "ok"


def test_secret_names():
    assert secret_name("demo", "API_KEY") == "skill.demo.API_KEY"
    assert env_name("my-key.1") == "PIYO_SECRET_MY_KEY_1"


# -- the tool --------------------------------------------------------------------------------


@pytest.fixture
def agent_parts(tmp_path, work):
    user = tmp_path / "user"
    builtin = tmp_path / "builtin"
    make_skill(user, name="theirs", scripts={"echo.py": ECHO})
    make_skill(builtin, name="ours", scripts={"echo.py": ECHO})
    skills = SkillRegistry(builtin_dir=builtin, user_dir=user)
    tools = ToolRegistry(core_tools() + script_tools(runner(work), skills))
    return skills, tools


def test_third_party_scripts_ask_every_time_but_built_in_ones_do_not(agent_parts):
    skills, tools = agent_parts
    tool = tools.get("skill.run_script")
    assert tool.risk_of({"skill": "theirs", "script": "echo.py"}) is Risk.CONFIRM
    assert tool.risk_of({"skill": "ours", "script": "echo.py"}) is Risk.AUTO
    assert tool.risk_of({"skill": "nope", "script": "x"}) is Risk.CONFIRM


@needs_uv
async def test_tool_requires_a_loaded_skill_asks_first_and_fences_the_result(agent_parts):
    skills, tools = agent_parts
    asked = []

    async def approver(req):
        asked.append(req.summary)
        return True

    script = Script(
        [TurnDone(tool_calls=[call("skill.run_script", skill="theirs", script="echo.py", args={"a": 1})])],
        [TurnDone(tool_calls=[call("load_skill", name="theirs", _id="c2")])],
        [TurnDone(tool_calls=[call("skill.run_script", skill="theirs", script="echo.py", args={"a": 1}, _id="c3")])],
        [TurnDone(text="done")],
    )
    agent = Agent(PROVIDER, "m", tools, skills, PermissionGate(approver), turn_fn=script)
    from piyo.models.turn import Message

    events = [e async for e in agent.run([Message(role="user", content="go")])]
    results = [e for e in events if isinstance(e, ToolFinished)]
    assert "Load the skill 'theirs' first" in results[0].output and results[0].is_error
    summary = "Run the script echo.py of the skill theirs with " + json.dumps({"a": 1})
    assert asked == [summary, summary]  # asked before the check that the skill is loaded, and again for the real run
    assert "<untrusted_content" in results[2].output and '"got"' in results[2].output
    assert sys.version_info >= (3, 12)
