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

These permissions are worldwide, nonexclusive and royalty-free, always limited to content the maintenance team actually has authority to license. They do not waive independent obligations under third-party original licenses or automatically extend the two contributors' permissions to all contributors. For versions expressly adopting this agreement and modifications made under Section VII, paragraphs 6 through 10, the special permissions are not retrospectively revoked by leaving the maintenance team or by later general licensing-policy changes; they also apply to later versions that still expressly adopt this agreement.

## Historical MIT rights and the new license's starting point

The maintainer's 2026-10-15 decision to adopt MIT remains part of the history. The former top-level MIT text is preserved verbatim in [LICENSES/MIT.txt](../LICENSES/MIT.txt), including `Copyright (c) 2026 FundsTrace contributors` and all permissions, conditions and disclaimers. G6, Tabler Icons, React/React DOM, Python/Electron dependencies and code adapted from the Analytix installer remain governed by [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and their respective original licenses.

| Git record | Confirmed state |
| --- | --- |
| Main before adoption | [`6361541c05ec`](https://github.com/Eysn0130/FundsTrace/commit/6361541c05ecd9a2d9d5948c6d1f0d16ee872cb5) |
| First commit including and adopting the agreement | [`22278954c44b`](https://github.com/Eysn0130/FundsTrace/commit/22278954c44bdb33d99106423fcb7c6c7533791e) |
| Merge into main | [PR #36](https://github.com/Eysn0130/FundsTrace/pull/36), merge commit [`adb2bd246ec5`](https://github.com/Eysn0130/FundsTrace/commit/adb2bd246ec57f1a0383fa8ac330e8a9a541f7be); 2026-10-21 (America/Los_Angeles; 10-22 in UTC) |

The new license applies from the first adopting source snapshot, within the scope of the team's authority to license. These records identify source states, not ownership of all historical material. Existing history, tags, Releases and installers are not relabeled.

The following boundaries govern the applicable scope:

1. Recipients may continue using, modifying, redistributing and commercially using code previously lawfully provided under MIT, including those portions carried into the current snapshot, subject to MIT's notice requirements when copying. Preserving the MIT text does not make MIT an alternative license for all future new content covered by the community license.
2. The new agreement's restrictions apply only to content the maintenance team owns or is authorized to license on those conditions. Placing old code and new content in the same file, repository or installer cannot retrospectively withdraw existing permissions. Distributions of mixed content must retain the notices required for each part.
3. The maintainer has expressly confirmed that CodeHaven969 and linzijie6524 granted the full permissions needed for their contributions within their authority to adopt this agreement, grant separate institutional/commercial permissions and provide the corresponding designated special permissions. This is not a copyright assignment and does not itself establish other authors' authorization.
4. The project preserves the provenance record of the maintenance snapshot of the original colleague's application and does not claim that the maintainer originally authored all existing code. The maintainer has now expressly confirmed that the original colleague's code initially taken over falls within the comprehensive authorization already confirmed for CodeHaven969 or linzijie6524. This records the maintainer's confirmation, not an independent review of the original authorization documents, and does not imply equivalent authorization for other unconfirmed materials. Historical MIT and third-party original licenses remain effective. The full provenance record is in the source repository at `docs/source-boundaries.md`, a development document not included in the installer.
5. Before accepting future external contributions, establish the contribution's community-license permissions and the authority needed for the team to grant separate institutional/commercial permissions and the designated-contributor permissions. A normal PR or merge is not itself a copyright assignment or unlimited relicensing permission. Full collaboration requirements are in the source repository at `CONTRIBUTING.md`, a development document not included in the installer.

## Making the protection effective

- **Identify the protected content.** Historical MIT code may still lawfully be modified and used commercially. The first adopting commit mainly changes licensing and distribution configuration; it does not establish that the team has acquired exclusive commercial rights to all existing functionality. Record the provenance and scope of new original work and contributions by path, commit and applicable license, keeping historical code distinct from newly restricted content.
- **Retain a valid basis for authorization.** Before granting separate institutional or commercial permissions, retain verifiable, non-public confirmation of the actual rightsholder's identity and authority to license, the subject matter and scope, and the relevant sublicensing and designated-special-permission rights. This guide records the maintainer's confirmation; it does not claim that all authors have signed written authorizations or that ownership has been comprehensively verified.
- **Address violations with evidence.** Section IX provides for automatic termination of the rights granted by this agreement upon violation of its license conditions, while preserving other valid licenses and statutory rights. For suspected infringement, preserve the relevant code or distributed artifacts and dates, comparisons with protected content, and applicable license and authorization records, so an entitled party can pursue available legal remedies. The terms alone cannot guarantee success in a dispute.
- **Distinguish distribution checks from runtime controls.** Resource allowlists and byte checks detect omissions or content drift in our packaging; they are not runtime access controls or copy protection. This licensing change has not added installation-time click-through acceptance or an in-application license viewer, and the activation-free policy remains unchanged.

## License materials to include in distributions

- The source repository, desktop backend resources and standalone Issuer resource configuration retain six materials: `LICENSE`, `LICENSE.en`, `LICENSES/MIT.txt`, `THIRD_PARTY_NOTICES.md`, `docs/licensing.md` and `docs/licensing.en.md`.
- The main desktop and Issuer application packages must also include `LICENSE` and `LICENSE.en` at the application-package root, so `SEE LICENSE IN LICENSE` in `package.json` points to the Chinese text and an English reference is available. Each third party's original license and existing resources remain included.

These are ongoing distribution-configuration requirements, not evidence that a particular installer has been built or accepted. Existing released artifacts retain their original licenses. Local links in this guide point to packaged materials; other development documents are identified by source-repository paths. For licensing background, see [GitHub's legal guide](https://opensource.guide/legal/).
