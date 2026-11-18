// Gemlock rank sunucusu: Gemlock kullanıcıları için Valorant rank'ını HenrikDev API'den çeker.
// - Henrik anahtarı sadece burada, HENRIK_KEY ortam değişkeninde durur; programda ya da GitHub'da yoktur.
// - Cevaplar Vercel CDN'inde 30 dk önbelleğe alınır (aynı hesap tekrar sorulunca Henrik'e gidilmez).
// - Hiçbir veri saklanmaz ya da kaydedilmez; sadece herkese açık Riot ID'ler işlenir.

const REGIONS = new Set(["eu", "na", "ap", "kr", "latam", "br"]);
const NAME_RE = /^[^#\u0000-\u001f]{1,16}$/u; // Riot oyun adı: en fazla 16 karakter
const TAG_RE = /^[\p{L}\p{N}]{2,5}$/u;        // Riot etiketi: 2-5 harf/rakam

// Aynı sunucu örneği içinde kaba bir hız sınırı (Henrik: dakikada 30)
const WINDOW_MS = 60_000;
const LIMIT = 28;
let stamps = [];

function send(res, status, body, cache) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", cache || "no-store");
  res.end(JSON.stringify(body));
}

module.exports = async (req, res) => {
  if (req.method !== "GET") return send(res, 405, { error: "method" });
  if (!req.headers["x-gemlock"]) return send(res, 403, { error: "client" });

  const key = process.env.HENRIK_KEY;
  if (!key) return send(res, 503, { error: "not_configured" });

  const url = new URL(req.url, "http://x");
  const region = (url.searchParams.get("region") || "eu").toLowerCase();
  const name = (url.searchParams.get("name") || "").trim();
  const tag = (url.searchParams.get("tag") || "").trim();
  if (!REGIONS.has(region) || !NAME_RE.test(name) || !TAG_RE.test(tag)) {
    return send(res, 400, { error: "bad_riot_id" }, "public, s-maxage=86400");
  }

  const now = Date.now();
  stamps = stamps.filter((t) => now - t < WINDOW_MS);
  if (stamps.length >= LIMIT) {
    res.setHeader("Retry-After", "20");
    return send(res, 429, { error: "busy" });
  }
  stamps.push(now);

  const henrik = `https://api.henrikdev.xyz/valorant/v3/mmr/${region}/pc/${encodeURIComponent(name)}/${encodeURIComponent(tag)}`;
  let r;
  try {
    r = await fetch(henrik, { headers: { Authorization: key, "User-Agent": "Gemlock-Server" } });
  } catch (e) {
    return send(res, 502, { error: "upstream" });
  }

  if (r.status === 429) {
    res.setHeader("Retry-After", r.headers.get("retry-after") || "30");
    return send(res, 429, { error: "busy" });
  }
  if (r.status === 404) {
    return send(res, 404, { error: "not_found" }, "public, s-maxage=3600");
  }
  if (!r.ok) return send(res, 502, { error: "upstream", status: r.status });

  const json = await r.json().catch(() => ({}));
  const d = json.data || {};
  // sadece Gemlock'un kullandığı alanlar
  const data = { current: d.current || null, peak: d.peak || null, seasonal: d.seasonal || [] };
  return send(res, 200, { data }, "public, s-maxage=1800, stale-while-revalidate=86400");
};
