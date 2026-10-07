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
- [~] P2 guth-pictures-ai-9a45a1 Rate limit na přihlášení do klientské galerie
  V app/api/gallery/[slug]/auth/route.ts chybí ochrana proti zkoušení hesel hrubou silou. Stejně jako v app/api/proposal/[slug]/auth/route.ts doplnit checkRateLimit z lib/rate-limit.ts s klíčem `gallery-auth:${slug}:${ip}` (IP z x-vercel-forwarded-for nebo x-real-ip, limit 5 pokusů za 15 min). Po překročení vracet 429. Přidat test app/api/gallery/[slug]/auth/__tests__/route.test.ts s mockem gallery libu a rate-limitu.
  Hotovo = Nový test ověří, že při checkRateLimit=false route vrátí 429 a nevolá verifyGalleryPassword, a že se správným heslem nastaví cookie. Vitest i tsc --noEmit projdou.
- [~] P2 guth-pictures-ai-7954d0 Expirace a povinný secret u tokenů klientské galerie
  Funkce verifyGalleryToken v modules/gallery/lib/gallery-auth.ts nekontroluje timestamp z payloadu, takže podepsaný token platí navždy. Navíc getHmacKey při chybějícím AUTH_SECRET použije "fallback-dev-secret". Doplnit kontrolu stáří tokenu (max 30 dní, stejně jako maxAge cookie v auth route) podle vzoru lib/auth-edge.ts a při chybějícím AUTH_SECRET token nepodepsat ani neověřit. Přidat modules/gallery/lib/__tests__/gallery-auth.test.ts.
  Hotovo = Testy v gallery-auth.test.ts ověří: platný token projde, token se starým timestampem (fake timers) neprojde, token s jiným slugem nebo upraveným podpisem neprojde, bez AUTH_SECRET verify vrátí false. Vitest i tsc projdou.
- [~] P2 guth-pictures-ai-ab1a46 Unit testy pro modules/gallery/lib/gallery.ts
  Vytvořit modules/gallery/lib/__tests__/gallery.test.ts s mockem getKv z @/lib/kv (vzor lib/__tests__/kv.test.ts a modules/brand-shoot/lib/__tests__/proposals.test.ts). Pokrýt createGallery (duplicitní slug hodí chybu, výpočet expiresAt, sadd do indexu), listGalleries (filtrace null), saveGallerySelection (zachová submittedAt), deleteGallery (smaže všechny klíče), verifyGalleryPassword, isExpired a chování bez KV.
  Hotovo = Soubor gallery.test.ts existuje, pokrývá všechny exportované funkce a npx vitest run v něm hlásí 0 failů. tsc --noEmit projde.
- [~] P2 guth-pictures-ai-bb67ea Unit testy pro Lightroom klienta (refresh tokenů)
  Vytvořit modules/gallery/lib/__tests__/lightroom.test.ts. Mockovat getKv a globální fetch a otestovat saveInitialTokens (expiresAt s 1min bufferem), refresh access tokenu (zachování starého refresh tokenu, když ho odpověď nevrátí, a chyba při !res.ok) a exportované API funkce getCatalogId, listAlbums a getAlbumAssets včetně parsování odpovědí. Před psaním assertionů přečíst implementaci.
  Hotovo = Soubor lightroom.test.ts pokrývá refresh tokenu a aspoň 3 exportované funkce. Vitest hlásí 0 failů, tsc --noEmit projde.
- [~] P2 guth-pictures-ai-2a2fc0 Testy pro app/sitemap.ts
  Přidat app/__tests__/sitemap.test.ts s mockem getBlogPosts z @/modules/blog/lib/blog. Ověřit, že sitemap obsahuje statické stránky i /sluzby/{slug} pro obě locale s alternates (cs, en, x-default) a že obsahuje jen publikované blogové posty s lastModified z updatedAt.
  Hotovo = sitemap.test.ts ověří počet záznamů, alternates a vyřazení nepublikovaného postu. Vitest i tsc --noEmit projdou.

## akano3
- [x] P1 ak-01 Unit testy pro lib/admin-auth.ts → https://github.com/RandomUsernameForMe/akano3/pull/1
  Přidej node:test testy do lib/admin-auth.test.mjs (stejný styl jako lib/wiki-links.test.mjs, import z "./admin-auth.ts") pro adminGuard.
  Pokryj: bez ADMIN_TOKEN vrací 503, chybějící hlavička x-admin-token 401, špatný token stejné i jiné délky 401, správný token vrací null.
  Proměnnou ADMIN_TOKEN obnov po každém testu. Produkční kód neměň. Hotovo = node --test lib/*.test.mjs projde včetně nových testů.
- [~] P2 akano3-ai-1c304f Oprava splitPointsWeighted: záporné podíly při zaokrouhlení
  Funkce splitPointsWeighted v lib/utils.ts přesouvá celou chybu zaokrouhlení do posledního podílu, takže ten může vyjít záporný. Například total=4 a 7 stejných vah dá [1,1,1,1,1,1,-2]. Funkci přesuň do nového čistého modulu lib/split-points.ts, který nebude importovat ./data, a lib/utils.ts ji z něj re-exportuj, aby import v app/api/admin/backfill-points/route.ts fungoval dál. Přepiš ji na metodu největších zbytků (floor a zbytek rozdělit podle největších desetinných částí). Prázdné váhy a total=0 musí fungovat dál. Napiš lib/split-points.test.mjs.
  Hotovo = Test v lib/split-points.test.mjs ověří, že pro total=4 a 7 stejných vah jsou všechny podíly ≥ 0 a součet je 4. Dále ověří součet přesně rovný total pro několik náhodných vstupů a [] pro prázdné váhy. Projde node --test lib/*.test.mjs i npx tsc --noEmit.
- [~] P2 akano3-ai-2b016f Unit testy pro rozpočet bodů v lib/utils.ts
  Napiš lib/utils.test.mjs pro resolveTargetCharacters (student/team/unit/circle, neznámé ID → []), teamsOfChars (deduplikace), resolvedCharsOf (fallback na legacy resolvedTeamIds) a teamDeltaOf (nový per-student režim i legacy týmový režim). lib/utils.ts importuje './data' bez přípony, což node --test nevyřeší. Pokud import selže, přesuň tyto čisté funkce do samostatného modulu, třeba lib/point-resolution.ts s importem './data.ts', povol allowImportingTsExtensions jen pokud to tsc vyžaduje, a v utils.ts je re-exportuj.
  Hotovo = lib/utils.test.mjs (případně lib/point-resolution.test.mjs) obsahuje alespoň jeden test pro každou z funkcí resolveTargetCharacters, teamsOfChars, resolvedCharsOf a teamDeltaOf včetně legacy větve. Projde node --test lib/*.test.mjs i npx tsc --noEmit.
- [~] P2 akano3-ai-140dd0 Validace vstupu POST /api/points
  app/api/points/route.ts nekontroluje amount (NaN, string, desetinné číslo, 0), targetType mimo team/unit/circle/student ani prázdný výsledek resolveTargetCharacters. Zapíše tak do point_log záznam, který nic nepřipíše, nebo nevalidní hodnotu. Vytvoř čistou funkci validatePointRequest v novém lib/point-validation.ts, která vrátí chybovou zprávu nebo null, a přesuň do ní i stávající pravidlo pro Růži (jen student, ±5). Route při chybě vrátí 400 a při prázdném seznamu cílů také 400. Napiš lib/point-validation.test.mjs.
  Hotovo = lib/point-validation.test.mjs pokrývá tyto případy: platný požadavek → null; amount NaN, 1.5 a 0 → chyba; neznámý targetType → chyba; Růže s targetType team nebo amount 6 → chyba. Route validatePointRequest používá. Projde node --test lib/*.test.mjs i npx tsc --noEmit.
- [~] P2 akano3-ai-706986 Validace těla wiki článku a úrovně kaichi v API
  POST /api/wiki (app/api/wiki/route.ts) a PUT /api/wiki/[id] neověřují tělo požadavku: chybějící slug nebo title, kaichiRequired mimo 0–8 nebo necelé číslo, nečíselný sortOrder. Chyba pak skončí jako 500 se String(err). POST /api/kaichi při nečíselném level zapíše NaN. Vytvoř lib/wiki-validation.ts s čistými funkcemi validateWikiArticleInput(body) a parseKaichiLevel(value) a použij je v těch třech routách: při chybě vrátí 400, PUT navíc 400 pro nečíselné id. Napiš lib/wiki-validation.test.mjs.
  Hotovo = lib/wiki-validation.test.mjs ověří tyto případy: platný článek → ok; prázdný slug, kaichiRequired 9 a -1 nebo 'x' → chyba; parseKaichiLevel('abc') → chyba, 3.7 → chyba nebo zaokrouhlení podle zvoleného chování. Routy funkce volají. Projde node --test lib/*.test.mjs i npx tsc --noEmit.

## secondbrain
- [~] P2 secondbrain-ai-67ff85 Bot: explicitní UTF-8 při čtení/zápisu souborů vaultu a stavu
  V bot/vault.py (create_subject, read_note, write_note, append_thought, read_backlog, mark_backlog_asked, mark_woven, to_inbox, recent_thoughts, write_digest) a bot/models.py (load/save stavu) se volá open() bez encoding, takže na Windows se použije cp1250/cp1252. Doplnit encoding="utf-8" do všech volání open() a přidat testy do bot/test_vault.py, které zapíšou/přečtou český text (např. 'Zpracováno', 'příliš žluťoučký') a ověří přesný obsah souboru čtený jako UTF-8 bajty.
  Hotovo = Nový test v bot/test_vault.py zapíše thought/subject/digest s diakritikou, přečte soubor přes open(..., encoding='utf-8') i read_bytes().decode('utf-8') a obsah sedí; `..\_venv\Scripts\python -X utf8 -m pytest bot -q` projde a grep 'open(' v bot/vault.py a bot/models.py nenajde volání bez encoding.
- [~] P2 secondbrain-ai-a5380b Bot: /subject s backslashem nebo znaky zakázanými ve Windows nesmí uniknout ze Subjects/
  bot/vault.py `_slug` nahrazuje jen '/', takže `/subject ..\..\x` vytvoří na Windows soubor mimo Subjects/ a názvy s ':', '?', '*', '"', '<', '>', '|' způsobí OSError. Rozšířit _slug o náhradu '\\' a znaků zakázaných ve Windows (na '-'), odmítnout/ošetřit prázdný název a názvy tvořené jen tečkami; subject_path ověří, že výsledná cesta leží uvnitř subjects_dir. Přidat testy do bot/test_vault.py.
  Hotovo = Testy v bot/test_vault.py: create_subject s názvem '..\\..\\evil' a 'A: B?' vytvoří soubor uvnitř tmp_path/Subjects (os.path.commonpath == subjects_dir) a nevyhodí výjimku; pytest bot projde.
- [~] P2 secondbrain-ai-8bce20 Web: stripFrontmatter musí zvládnout CRLF konce řádků
  web/lib/markdown.ts `stripFrontmatter` kontroluje jen `---\n`, zatímco noteType ve web/lib/vault.ts už počítá s `\r?\n`. Poznámky uložené na Windows (CRLF) tak v NotePanelu zobrazí YAML hlavičku. Upravit regex na `\r?\n` a doplnit testy do web/tests/vault.test.ts (describe stripFrontmatter): CRLF hlavička se odstraní, neukončený CRLF blok zůstane netknutý.
  Hotovo = Nový test `stripFrontmatter("---\r\ntype: subject\r\n---\r\n# Hodnoty\r\n")` vrátí "# Hodnoty\r\n"; `npm test --prefix web` projde.
- [~] P2 secondbrain-ai-f3844b Web API: /api/capture a /api/note vrací 400 na nevalidní vstup místo 500
  web/app/api/capture/route.ts volá `text?.trim()` bez kontroly typu — `{text: 5}` nebo nevalidní JSON skončí neošetřenou výjimkou (500). web/app/api/note/[...path]/route.ts PUT při nevalidním JSON spadne mimo try a nestringový content hlásí zavádějící 'invalid path'. Ošetřit parsování JSON (400 'invalid json'), ověřit typ text/content jako string (400 'invalid body'). Testy doplnit do web/tests/api.test.ts.
  Hotovo = Nové testy v web/tests/api.test.ts: POST /api/capture s `{text: 5}` a s nevalidním JSON → 400; PUT /api/note s `{content: 5}` → 400 s chybou jinou než 'invalid path'; `npm test --prefix web` projde.
- [~] P2 secondbrain-ai-058606 Bot: mark_backlog_asked odškrtne jen přesně odpovídající otázku
  bot/vault.py `mark_backlog_asked` hledá první řádek `- [ ] ` končící na text otázky (endswith), takže krátká otázka (např. 'Proč?') odškrtne jiný řádek, který jen končí stejně. Porovnávat stejně jako read_backlog — regexem vytáhnout otázku z řádku a porovnat přesnou shodu po strip(). Přidat test do bot/test_vault.py se dvěma řádky, kde jedna otázka je suffixem druhé.
  Hotovo = Test: backlog s řádky '- [ ] A: Co dál a proč?' a '- [ ] B: proč?'; mark_backlog_asked(path, 'proč?') odškrtne jen řádek B, read_backlog vrátí jen otázku A; pytest bot projde.
