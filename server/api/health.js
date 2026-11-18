module.exports = (req, res) => {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify({ ok: true, configured: Boolean(process.env.HENRIK_KEY) }));
};
