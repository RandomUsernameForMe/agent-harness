# Backlog

## guth-pictures
- [ ] P1 gp-01 Unit testy pro lib/theme.ts
  Přidej vitest testy (tests vedle stávajících, stejný styl) pro lib/theme.ts.
  buildThemeStyle: vrátí `:root{...}` s deklarací pro každý klíč z COLOR_KEYS podle CSS_VAR_MAP.
  getThemeColors: vrátí defaultColors, když getKv() vrátí null, když KV nemá uložená data, a když kv.get hodí výjimku; jinak vrátí uložené barvy.
  Mockuj "server-only" a ./kv. Produkční kód neměň. Hotovo = npm test zelený včetně nových testů.
- [ ] P2 gp-02 Unit testy pro lib/auth.ts
  Přidej vitest testy pro veřejné funkce lib/auth.ts, pokryj úspěšnou i zamítnutou cestu.
  Mockuj "server-only" a závislosti mimo modul. Produkční kód neměň. Hotovo = npm test zelený včetně nových testů.
