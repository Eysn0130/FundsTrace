# Licensing and authorization guide

[中文](licensing.md) · English

The maintained version adopts the [FundsTrace Community Use and Source License 1.0](../LICENSE), adopted by the maintainer on 2026-10-21. This is a custom source-available license, not MIT, GPL, AGPL or an OSI-approved standard open-source license. The [English license](../LICENSE.en) and this English guide are references; the Chinese `LICENSE` prevails if interpretations differ. This guide helps identify the applicable scope; specific rights are governed by the complete agreement and each part's valid original license.

The outward-facing name is “循迹 FundsTrace 项目维护团队” (FundsTrace Project Maintenance Team), and the routine licensing contact is Eysn0130. Send licensing requests to **eysn0130@gmail.com** or **402168648@qq.com**. Separately issued institutional or commercial permissions should identify the actual licensing party, representative, authority and scope. The team name does not itself state that the team is a separately registered legal person or organization.

## General scope

| Activity | Community license rule |
| --- | --- |
| Personal learning, research and noncommercial personal analysis | Free; performing duties for an organization, handling its business or providing paid services is outside this scope |
| Fixing issues, adapting formats and developing new features | Permitted; changes used only locally for personal use need not be published |
| Distributing modified versions free of charge | Retain notices, publish complete corresponding source, provide relevant changes under the same community license and identify the version as an unofficial community modification |
| Installation, trials, testing or business use by companies, public bodies, schools or other organizations | Prior written permission is required, including nonprofit or free-of-charge use |
| Commercial integration, paid services, sales or online functionality for third parties | Prior permission is required, except within the designated contributors' special permissions |
| Standalone reskinning or renaming of FundsTrace as one's own product, or resale | Prohibited without explicit written permission |

Handling an organization's business on a personal computer or account remains institutional use. License conditions are separate from whether the software can start: the community license does not restore an activation-code gate, change the confirmed activation-free startup policy for v0.1.x or change desktop request authentication.

Source-publication obligations do not include cases, identities, accounts, transactions, photos, credentials, keys or other business data; separate that material from distributable code. Analytical reports do not automatically acquire the software license merely because the software generated them, but the institutional or commercial use producing the reports still requires the relevant permission.

## Special permissions for designated contributors

See [Section 7 of LICENSE](../LICENSE) for the complete terms. CodeHaven969 and linzijie6524:

- Retain their respective lawful copyrights and rights to continue developing, using, connecting their own code to Agents and licensing it separately; their contribution permissions to the project are nonexclusive.
- May continue project research and development, Agent feature development, Agent-assisted development, and private debugging, building and testing without a license fee or having to reapply for that development solely because of institutional affiliation.
- May integrate FundsTrace into other products that each is lawfully entitled to develop, operate or deliver, including closed-source and commercial products, and perform necessary modifications, deployment, commercial delivery and online services without reapplying for each integration or product version already covered by the permission.
- Are exempt, within that integration scope, from this agreement's corresponding-source publication and community-modification distribution requirements; integrated products may use their own names but must retain truthful copyright, origin, license and third-party notices.
- May provide necessary permissions to the relevant product's development, operating or delivery entities, necessary personnel and entrusted service providers within the product and task scope expressly defined by the agreement. Customers may receive the nonexclusive rights necessary to copy, install, run, back up and use the integrated product for internal institutional or commercial business without applying individually to this project. This does not grant standalone commercial licensing to unrelated third parties, or standalone reskinning and resale outside the integrated product.

These permissions are worldwide, nonexclusive and royalty-free, always limited to content the maintenance team actually has authority to license. They do not waive independent obligations under third-party original licenses or automatically extend the two contributors' permissions to all contributors. For versions expressly adopting this agreement and modifications made under it, the special permissions are not retrospectively revoked by leaving the maintenance team or by later general licensing-policy changes; they also apply to later versions that still expressly adopt this agreement.

## Historical MIT rights and the new license's starting point

The maintainer's 2026-10-15 decision to adopt MIT remains part of the history. The former top-level MIT text is preserved verbatim in [LICENSES/MIT.txt](../LICENSES/MIT.txt), including `Copyright (c) 2026 FundsTrace contributors` and all permissions, conditions and disclaimers. G6, Tabler Icons, React/React DOM, Python/Electron dependencies and code adapted from the Analytix installer remain governed by [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and their respective original licenses.

The main baseline before adoption is `6361541c05ecd9a2d9d5948c6d1f0d16ee872cb5`. It identifies only the committed state before adoption; **it is neither a commit already containing the new agreement nor proof of ownership of all historical material**.

The new license starts with the first source snapshot that expressly includes and adopts this agreement. Identify the first applicable commit and subsequent releases from actual Git records; do not substitute the pre-adoption baseline for that commit or invent a SHA or version number that does not yet exist. Existing history, tags, Releases and installers are not relabeled with the new license.

The following boundaries govern the applicable scope:

1. Recipients may continue using, modifying, redistributing and commercially using code previously lawfully provided under MIT, including those portions carried into the current snapshot, subject to MIT's notice requirements when copying. Preserving the MIT text does not make MIT an alternative license for all future new content covered by the community license.
2. The new agreement's restrictions apply only to content the maintenance team owns or is authorized to license on those conditions. Placing old code and new content in the same file, repository or installer cannot retrospectively withdraw existing permissions. Distributions of mixed content must retain the notices required for each part.
3. The maintainer has expressly confirmed that CodeHaven969 and linzijie6524 granted the full permissions needed for their contributions within their authority to adopt this agreement, grant separate institutional/commercial permissions and provide the corresponding designated special permissions. This is not a copyright assignment and does not itself establish other authors' authorization.
4. The project preserves the provenance record of the maintenance snapshot of the original colleague's application and does not claim that the maintainer originally authored all existing code. Whether the original colleague's code falls within the above contributors' authorization has not been separately clarified; that uncertainty does not expand authorization or revoke an original license. The full provenance record is in the source repository at `docs/source-boundaries.md`, a development document not included in the installer.
5. Before accepting future external contributions, establish the contribution's community-license permissions and the authority needed for the team to grant separate institutional/commercial permissions and the designated-contributor permissions. A normal PR or merge is not itself a copyright assignment or unlimited relicensing permission. Full collaboration requirements are in the source repository at `CONTRIBUTING.md`, a development document not included in the installer.

## License materials to include in distributions

The source repository retains `LICENSE`, `LICENSE.en`, `LICENSES/MIT.txt`, `THIRD_PARTY_NOTICES.md`, `docs/licensing.md`, `docs/licensing.en.md` and each third party's original license. The desktop backend resource allowlist and standalone Issuer resource configuration must include these license texts, historical MIT text, notices and bilingual guides. The main desktop and Issuer application packages must also include `LICENSE` and `LICENSE.en` at the application-package root, so the `SEE LICENSE IN LICENSE` metadata in `package.json` points to the Chinese text and an English reference is available. Do not remove existing third-party resources as a result.

These are ongoing source and distribution-configuration requirements, not evidence that a particular installer has been built or accepted. Existing released artifacts retain the licenses under which they were released. This guide uses clickable local links only for materials included in the package; other development documents are identified by source-repository paths.

For background on licensing changes, ownership and third-party compatibility, see [GitHub's legal guide](https://opensource.guide/legal/). The legal application of custom terms depends on the actual licensing party, authority and applicable law; checking text and configuration does not establish legal enforceability.
