"""Build installation-specific app packages without requiring Python on the host."""
import json
import os
import zipfile
from pathlib import Path

root = Path("/apps")
origin = os.environ["UNIBOT_PUBLIC_ORIGIN"].rstrip("/")
package = root / "pptist"
manifest = json.loads((package / "aina.json").read_text())
manifest["tools"]["mcp"]["url"] = f"{origin}/pptist/mcp"
manifest["canvas"]["url"] = f"{origin}/pptist/"
with zipfile.ZipFile(root / "pptist.aina.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("aina.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.name != "aina.json":
            archive.write(path, path.relative_to(package))
(root / "drawio-mcp.json").write_text(json.dumps({
    "mcpServers": {"drawio": {"url": f"{origin}/drawio/mcp"}}
}, indent=2) + "\n")
print(f"Prepared /apps/pptist.aina.zip and drawio-mcp.json for {origin}")
