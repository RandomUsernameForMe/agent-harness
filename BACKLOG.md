# Backlog

## guth-pictures
- [x] P1 gp-01 Unit testy pro lib/theme.ts → https://github.com/RandomUsernameForMe/guth-pictures/pull/6
  Přidej vitest testy (tests vedle stávajících, stejný styl) pro lib/theme.ts.
  buildThemeStyle: vrátí `:root{...}` s deklarací pro každý klíč z COLOR_KEYS podle CSS_VAR_MAP.
  getThemeColors: vrátí defaultColors, když getKv() vrátí null, když KV nemá uložená data, a když kv.get hodí výjimku; jinak vrátí uložené barvy.
  Mockuj "server-only" a ./kv. Produkční kód neměň. Hotovo = npm test zelený včetně nových testů.
- [x] P2 gp-02 Unit testy pro lib/auth.ts → https://github.com/RandomUsernameForMe/guth-pictures/pull/7
  Přidej vitest testy pro veřejné funkce lib/auth.ts, pokryj úspěšnou i zamítnutou cestu.
  Mockuj "server-only" a závislosti mimo modul. Produkční kód neměň. Hotovo = npm test zelený včetně nových testů.
- [x] P1 gp-03 Unit testy pro lib/kv.ts → https://github.com/RandomUsernameForMe/guth-pictures/pull/8
  Přidej vitest testy do lib/__tests__/kv.test.ts (stejný styl jako ostatní testy tam) pro getKv.
  Pokryj: vrátí null bez KV_REST_API_URL, vrátí null bez KV_REST_API_TOKEN, s oběma proměnnými vrátí kv z "@vercel/kv".
  Mockuj "server-only" a "@vercel/kv", proměnné prostředí obnov po každém testu. Produkční kód neměň. Hotovo = npm test zelený včetně nových testů.

## akano3
- [x] P1 ak-01 Unit testy pro lib/admin-auth.ts → https://github.com/RandomUsernameForMe/akano3/pull/1
  Přidej node:test testy do lib/admin-auth.test.mjs (stejný styl jako lib/wiki-links.test.mjs, import z "./admin-auth.ts") pro adminGuard.
  Pokryj: bez ADMIN_TOKEN vrací 503, chybějící hlavička x-admin-token 401, špatný token stejné i jiné délky 401, správný token vrací null.
  Proměnnou ADMIN_TOKEN obnov po každém testu. Produkční kód neměň. Hotovo = node --test lib/*.test.mjs projde včetně nových testů.
