# Pinned dependency and license review

Reviewed 2026-10-17 against official project documentation, official upstream
license files, and the exact version metadata at `https://registry.npmjs.org/`.
`package-lock.json` pins transitive tarballs and integrity digests. Run `npm ci`
without rewriting the lock; `npm run licenses` refuses an unreviewed SPDX label
and regenerates the complete production/development inventory and runtime notices.

| Dependency | Pinned version | License | Official source / reason |
| --- | --- | --- | --- |
| React / React DOM | 19.3.1 | MIT | [React versions](https://react.dev/versions), [license](https://github.com/facebook/react/blob/main/LICENSE); maintained browser renderer |
| TypeScript | 6.0.3 | Apache-2.0 | [license](https://github.com/microsoft/TypeScript/blob/main/LICENSE.txt); strict compile checks; TypeScript 7 is outside the pinned typescript-eslint peer range `<6.1.0` |
| Vite / React plugin | 8.3.4 / 6.1.2 | MIT | [guide](https://vite.dev/guide/), [license](https://github.com/vitejs/vite/blob/main/LICENSE); local compiled assets |
| React Router DOM | 7.18.4 | MIT | [project license](https://github.com/remix-run/react-router/blob/main/LICENSE.md); nested organization paths |
| TanStack Query | 5.104.3 | MIT | [cancellation](https://tanstack.com/query/latest/docs/framework/react/guides/query-cancellation), [license](https://github.com/TanStack/query/blob/main/LICENSE); no persistence adapter |
| Radix Dialog | 1.3.1 | MIT | [accessibility](https://www.radix-ui.com/primitives/docs/overview/accessibility), [license](https://github.com/radix-ui/primitives/blob/main/LICENSE); focus-trapped dialog/drawer |
| Zod | 4.6.7 | MIT | [license](https://github.com/colinhacks/zod/blob/main/LICENSE); runtime validation of untrusted API JSON |
| Vitest / jsdom | 5.0.5 / 30.1.3 | MIT | [Vitest guide](https://vitest.dev/guide/), [license](https://github.com/vitest-dev/vitest/blob/main/LICENSE), [jsdom](https://github.com/jsdom/jsdom); component checks |
| Playwright | 1.64.1 | Apache-2.0 | [browser install](https://playwright.dev/docs/browsers), [license](https://github.com/microsoft/playwright/blob/main/LICENSE); real browser journeys |
| axe Playwright | 4.14.0 | MPL-2.0 | [axe license/source](https://github.com/dequelabs/axe-core/blob/develop/LICENSE); test-only accessibility analysis, not shipped in application bundle |
| ESLint / typescript-eslint | 10.13.0 / 8.71.2 | MIT | [ESLint](https://github.com/eslint/eslint/blob/main/LICENSE), [typescript-eslint](https://typescript-eslint.io/users/dependency-versions/); pinned peer compatibility |
| Testing Library React / DOM / user-event / jest-dom | 16.3.3 / 10.4.2 / 14.6.8 / 7.0.1 | MIT | [official organization](https://github.com/testing-library); semantic component interaction tests |

Node **24.22.0** satisfies Vite, Vitest and jsdom's engine constraints (jsdom
requires Node ≥24.15). Node 24 is an [official LTS line](https://nodejs.org/en/about/previous-releases).
The exact compiler/linter peer and engine ranges were checked from the installed
packages and official registry metadata. React hooks checks run alongside strict
TypeScript. Lockfile-only optional platform binaries are included in the license
inventory even when absent on the current machine.

The 38 runtime packages comprise 37 MIT packages and the
[0BSD tslib](https://github.com/microsoft/tslib/blob/main/LICENSE.txt) helper library. The complete locked tooling inventory
also includes Apache-2.0, ISC, BSD-2-Clause, BSD-3-Clause, MIT-0, 0BSD, CC0-1.0,
BlueOak-1.0.0, CC-BY-4.0 and MPL-2.0. The build/test-only caniuse data's
[CC-BY-4.0 attribution](https://github.com/browserslist/caniuse-lite/blob/main/LICENSE)
and [CSS Tools MIT-0](https://github.com/csstools/postcss-plugins/blob/main/LICENSE.md)
were reviewed explicitly. No modified MPL source is redistributed by the
application bundle. Source links for the development tools remain in the inventory.
The MIT `react-remove-scroll-bar` tarball omits its license file; its official
[upstream notice](https://github.com/theKashey/react-remove-scroll-bar/blob/master/LICENSE)
is preserved under `licenses/`, with provenance and digest. Runtime notices are
copied to `dist/licenses.txt`; deploy them with the compiled assets.
