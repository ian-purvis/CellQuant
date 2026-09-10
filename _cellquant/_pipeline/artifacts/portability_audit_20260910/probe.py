"""Read-only code probes. No install, torch import, model loading or network."""
import importlib.util,sys,tomllib,hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[2]
p=root/'src/cellquant/plugin/capabilities.py'
spec=importlib.util.spec_from_file_location('audit_caps',p);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
c=m.detect_runtime_capabilities(cellpose_version_fn=lambda:'4.2.1.1',cuda_fn=lambda:(False,None,None,'PyTorch not importable (missing DLL)',False,None))
print('NO IMPORTABLE TORCH: available engines =',[x.engine_id for x in c.available_engines]);print(c.summary)
assert c.available_engines and c.torch_version is None
manifest=tomllib.loads((root/'pyproject.toml').read_text());req=next(x for x in manifest['project']['dependencies'] if x.startswith('cellpose'))
print('CELLQUANT PACKAGE REQUIREMENT:',req);print('V3 INSTALLER REQUIREMENT PRESENT:', 'cellpose>=3.1.0,<4' in (root/'scripts/install_windows.ps1').read_text())
paths=['pyproject.toml','environment.yml','scripts/install_windows.ps1','scripts/resolve_env_location.ps1','scripts/resolve_cuda_torch.ps1','scripts/launch_napari.ps1','src/cellquant/plugin/capabilities.py','src/cellquant/persist/staging.py']
hashes={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
(Path(__file__).parent/'source_hashes.json').write_text(json.dumps(hashes,indent=2))
