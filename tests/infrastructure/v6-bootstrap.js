// Disposable environment only. Credentials never leave the mounted secret files.
const admin = db.getSiblingDB("admin");
if (!admin.auth("v6-test-root", require("fs").readFileSync("/run/secrets/root", "utf8"))) {
  throw new Error("disposable root not initialized");
}
try {
  rs.initiate({_id: "acp_v6_phase2", members: [{_id: 0, host: "mongodb:27017"}]});
} catch (error) {
  if (error.codeName !== "AlreadyInitialized") throw error;
}
