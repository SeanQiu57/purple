# config_loader.py －－简单 YAML 读取 & ${ENV_VAR} 展开
import os, yaml, pathlib, re
_cfg_path = pathlib.Path(
__file__
).with_name("az_agent.yaml")
def _expand(val: str):
    m = re.fullmatch(r"\$\{([^}]+)\}", val or "")
    return os.getenv(m.group(1), "") if m else val
with open(_cfg_path, encoding="utf-8") as f:
    raw_cfg = yaml.safe_load(f)
def _walk(node):
    if isinstance(node, dict):
        return {k: _walk(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk(x) for x in node]
    if isinstance(node, str):
        return _expand(node)
    return node
config = _walk(raw_cfg)