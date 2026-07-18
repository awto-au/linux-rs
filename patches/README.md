# patches/

`git format-patch` output for each landed translation unit (TU), kept here
as a readable, reviewable record of the series. These are **not yet**
submitted upstream.

## DCO policy for upstream submission

Any patch from this directory that is proposed to an upstream Linux tree
(mainline, `rust-for-linux`, or a subsystem maintainer's tree) must carry a
real `Signed-off-by:` trailer per the kernel's
[Developer's Certificate of Origin](https://www.kernel.org/doc/html/latest/process/submitting-patches.html#sign-your-work-the-developer-s-certificate-of-origin)
(`Documentation/process/submitting-patches.rst` in the kernel tree). The
patches currently in this directory do **not** carry `Signed-off-by`
trailers — they were generated as an internal working record, not as
upstream-ready submissions. Adding real DCO sign-off is a precondition for
sending any of them out, not a formality to skip.
