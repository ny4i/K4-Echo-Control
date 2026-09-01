# TR4W Linux packaging — staging only

**This is not part of K4-Echo-Control.** It is AppImage packaging for
[TR4W](https://github.com/TR4W/TR4W-D12), parked here because this session could
not attach the `TR4W` organization (cross-owner adds are unsupported) and could
not create a new repository (403).

## Moving it to TR4W-D12

The layout inside this directory is already correct **relative to the TR4W repo
root** — transplant it, don't rearrange it:

```sh
git checkout <this-branch> -- tr4w-linux-packaging
cp -r tr4w-linux-packaging/packaging /path/to/TR4W-D12/
cp -r tr4w-linux-packaging/.github   /path/to/TR4W-D12/
```

Equivalently, from the tarball, which already has this layout at its top level:

```sh
cd /path/to/TR4W-D12 && tar xzf tr4w-linux-packaging.tar.gz
```

Either way, `tr4w-linux-packaging/README.md` — this file — does **not** travel.
It describes the staging arrangement only. The documentation that matters is
`packaging/linux/README.md`, which includes the list of placeholders to correct
before the first build.

`build-appimage.sh` resolves the repo root as `../..` from its own location, and
the Dockerfile's build context is `packaging/linux`. Both break if the files move.

Note the workflow is nested at `tr4w-linux-packaging/.github/workflows/` on
purpose: at the K4-Echo-Control repo root it would be a live workflow and would
fire on every push to this repo. It only becomes active once it sits at the root
of TR4W-D12.

See `packaging/linux/README.md` for the actual documentation.
