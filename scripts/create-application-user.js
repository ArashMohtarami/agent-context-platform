const adminDatabase = db.getSiblingDB("admin");
const applicationDatabase = db.getSiblingDB(process.env.MONGODB_DATABASE);
const monitorRole = "applicationReplicaSetMonitor";
const migratorRole = "applicationSchemaMigrator";
const applicationRoles = [
    { role: "readWrite", db: process.env.MONGODB_DATABASE },
    { role: monitorRole, db: "admin" },
    { role: migratorRole, db: "admin" },
];
const narrowRoles = [
    {
        role: monitorRole,
        privileges: [
            {
                resource: { cluster: true },
                actions: ["replSetGetStatus"],
            },
        ],
    },
    {
        // Startup migrations update collection validators in place. This is the
        // only administrative action the application user is granted, and only
        // on its own database.
        role: migratorRole,
        privileges: [
            {
                resource: { db: process.env.MONGODB_DATABASE, collection: "" },
                actions: ["collMod"],
            },
        ],
    },
];

for (const definition of narrowRoles) {
    if (adminDatabase.getRole(definition.role) === null) {
        adminDatabase.createRole({
            role: definition.role,
            privileges: definition.privileges,
            roles: [],
        });
    } else {
        adminDatabase.updateRole(definition.role, {
            privileges: definition.privileges,
            roles: [],
        });
    }
}
if (applicationDatabase.getUser(process.env.MONGODB_APP_USERNAME) === null) {
    applicationDatabase.createUser({
        user: process.env.MONGODB_APP_USERNAME,
        pwd: process.env.MONGODB_APP_PASSWORD,
        roles: applicationRoles,
    });
} else {
    applicationDatabase.updateUser(process.env.MONGODB_APP_USERNAME, {
        pwd: process.env.MONGODB_APP_PASSWORD,
        roles: applicationRoles,
    });
}
