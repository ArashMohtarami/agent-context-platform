const adminDatabase = db.getSiblingDB("admin");
const synchronizationRoles = [{ role: "searchCoordinator", db: "admin" }];

if (adminDatabase.getUser(process.env.MONGOT_SYNC_USERNAME) === null) {
    adminDatabase.createUser({
        user: process.env.MONGOT_SYNC_USERNAME,
        pwd: process.env.MONGOT_SYNC_PASSWORD,
        roles: synchronizationRoles,
    });
} else {
    adminDatabase.updateUser(process.env.MONGOT_SYNC_USERNAME, {
        pwd: process.env.MONGOT_SYNC_PASSWORD,
        roles: synchronizationRoles,
    });
}
