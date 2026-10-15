const admin = db.getSiblingDB("admin");
if (!admin.auth("v6-test-root", require("fs").readFileSync("/run/secrets/root", "utf8"))) {
  throw new Error("disposable root authentication failed");
}
if (!db.hello().isWritablePrimary) throw new Error("disposable primary not ready");
if (!admin.getUser("v6-test-sync")) {
  admin.createUser({user: "v6-test-sync",
    pwd: require("fs").readFileSync("/run/secrets/sync", "utf8"),
    roles: [{role: "searchCoordinator", db: "admin"}]});
}
