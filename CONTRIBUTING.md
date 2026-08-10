# Contributing to atomiX

## Licensing of contributions

atomiX is MIT-licensed. By submitting a contribution you agree that it is
licensed under the same MIT License as the rest of the project.

Contributions are accepted under the
[Developer Certificate of Origin 1.1](https://developercertificate.org/). Sign
off each commit:

    git commit -s -m "your message"

which appends:

    Signed-off-by: Your Name <your.email@example.com>

The DCO is a statement that you wrote the contribution or otherwise have the
right to submit it under the project's licence. It is deliberately lighter than
a copyright-assignment CLA: **you keep copyright in your own work.** If the
project ever needs consolidated ownership — to relicense, or to dual-licence —
that requires a separate written agreement with each contributor, so raise it
before contributing anything substantial if that matters to you.

Record yourself in [AUTHORS.md](AUTHORS.md) in the same change.

## What a contribution needs

The project's standard is that a claim is backed by evidence, so:

- Say which evidence level a result belongs to. Simulation, synthesis,
  place-and-route, volatile board execution, and live reconfiguration are
  distinct and are never merged. See `docs/research-checklist.md`.
- A recorded number must reproduce from the command recorded beside it.
- Run the narrowest relevant test, then `make verify-smoke`, before proposing a
  change. `make nightly-integrated` is the broad suite.
- Update the owning checklist and design document in the same change.
- Do not commit generated build trees, bitstreams, or logs; record release
  SHA-256 identities instead.

Third-party code may only be added with a licence compatible with MIT, recorded
in [NOTICE](NOTICE).

## Reporting security issues

Do not open a public issue for a security problem. Email
<shubhendragautam1513@gmail.com> instead.
