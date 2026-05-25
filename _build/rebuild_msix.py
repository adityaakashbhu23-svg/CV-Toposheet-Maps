import subprocess
import shutil
import os

build_dir = r"C:\CV- Toposheet\_build"
staging = os.path.join(build_dir, "msix_staging")
output = os.path.join(build_dir, "CVToposheet_1.0.0.0_x64.msix")
makeappx = r"C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\makeappx.exe"

# Remove old files
if os.path.exists(output):
    os.remove(output)
    print("Removed old MSIX")

if os.path.exists(staging):
    shutil.rmtree(staging)
    print("Removed old staging folder")

# Create staging
os.makedirs(staging)
print("Created staging folder")

# Copy AppxManifest.xml
shutil.copy(os.path.join(build_dir, "AppxManifest.xml"), staging)
print("Copied AppxManifest.xml")

# Copy Assets folder
shutil.copytree(
    os.path.join(build_dir, "Assets"),
    os.path.join(staging, "Assets")
)
print("Copied Assets")

# Copy all app files from dist\CVToposheet
dist_dir = os.path.join(build_dir, "dist", "CVToposheet")
for item in os.listdir(dist_dir):
    src = os.path.join(dist_dir, item)
    dst = os.path.join(staging, item)
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
print(f"Copied app files from dist")

# Count files
file_count = sum(len(files) for _, _, files in os.walk(staging))
print(f"Total files in staging: {file_count}")

# Run MakeAppx
print("Running MakeAppx.exe...")
result = subprocess.run(
    [makeappx, "pack", "/d", staging, "/p", output, "/nv", "/o"],
    capture_output=True, text=True
)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
print("Return code:", result.returncode)

if os.path.exists(output):
    size_mb = os.path.getsize(output) / (1024 * 1024)
    print(f"\nSUCCESS! MSIX created: {size_mb:.1f} MB")
else:
    print("\nFAILED: MSIX not created")
