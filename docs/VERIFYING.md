# Verifying a release

Every release file is built by the `Release` workflow in this repository, carries a GitHub
build attestation, and is listed in `SHA256SUMS.txt`, which is signed keylessly with Sigstore.

Download the release files into one folder, then:

```sh
# 1. Each file was built by this repo's release workflow
gh attestation verify obs_director_mcp-<version>-py3-none-any.whl --repo rustybladerunner/obs-director-mcp
gh attestation verify obs_director_mcp-<version>.tar.gz           --repo rustybladerunner/obs-director-mcp

# 2. The checksum file itself is signed (bundle ships beside it)
gh attestation verify SHA256SUMS.txt --repo rustybladerunner/obs-director-mcp \
  --bundle SHA256SUMS.txt.sigstore

# 3. The files match the signed checksums
sha256sum -c SHA256SUMS.txt
```

On Windows PowerShell, replace step 3 with `Get-FileHash -Algorithm SHA256 <file>` and compare
against the matching line in `SHA256SUMS.txt`.

A release without these files, or one whose attestation fails, was not built by CI: do not use it.
The early release `v0.1.0-alpha.1` was uploaded by hand and has no attestation.
