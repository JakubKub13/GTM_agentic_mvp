/**
 * Full-page navigation helper (plan #18).
 *
 * Google SSO sign-in/out are *server-driven redirects* (plan #13:
 * `GET /auth/login` -> Google consent, `GET /auth/callback` -> session cookie),
 * not client-side React Router transitions. Going through one tiny helper keeps
 * `window.location` assignment in a single place so tests can mock it without
 * touching jsdom's read-only navigation.
 */
export function hardRedirect(url: string): void {
  window.location.assign(url);
}
