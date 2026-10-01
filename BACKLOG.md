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
