const adminDatabase = db.getSiblingDB("admin");
const expectedReplicaSet = process.env.MONGODB_REPLICA_SET;
const memberHost = `${process.env.MONGODB_HOST}:${process.env.MONGODB_PORT}`;
let status;
try {
    status = adminDatabase.runCommand({ replSetGetStatus: 1 });
} catch (error) {
    if (error.code !== 94) {
        throw error;
    }
    status = { ok: 0, code: error.code };
}

if (status.ok !== 1) {
    const initiate = adminDatabase.runCommand({
        replSetInitiate: {
            _id: expectedReplicaSet,
            members: [{ _id: 0, host: memberHost }],
        },
    });
    if (initiate.ok !== 1) {
        throw new Error(`Cannot initialize replica set (code ${initiate.code})`);
    }
} else if (status.set !== expectedReplicaSet) {
    throw new Error("Configured replica-set name does not match the existing replica set");
}
