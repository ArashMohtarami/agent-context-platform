"""Shared strict MongoDB document types."""

type MongoValue = object
type MongoDocument = dict[str, MongoValue]
