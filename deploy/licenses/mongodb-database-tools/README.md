# MongoDB Database Tools notices

The application image copies `mongodump` and `mongorestore` from the pinned
`mongo:8.3.9-noble` image. The included Database Tools identify themselves as
version **100.18.0**, source revision `21a342dfee6468ad9350d156d25086da64dd03b1`.
That image removes its packaged documentation, so this directory restores the
upstream notices alongside the binaries at `/usr/share/doc/mongodb-database-tools/`.

These files retain their upstream licenses; the repository's MIT license does
not replace them. Update and review these notices whenever the source image or
Database Tools version changes.

| File | Upstream source | SHA-256 |
| --- | --- | --- |
| `LICENSE.md` | [mongo-tools 100.18.0](https://github.com/mongodb/mongo-tools/blob/100.18.0/LICENSE.md) | `e0b215145cf51c766603856643a746420f60aedfbf9a523a2d82a251cac3cce3` |
| `THIRD-PARTY-NOTICES` | [mongo-tools 100.18.0](https://github.com/mongodb/mongo-tools/blob/100.18.0/THIRD-PARTY-NOTICES) | `6f72a5d4587d2a634273e5e7eafa0fd912d6ee66d2b5485fa459cb6387d887c8` |
| `APACHE-2.0.txt` | [Apache Commons Lang license](https://github.com/apache/commons-lang/blob/master/LICENSE.txt) | `b1d2870f1a00e4d7f56576e5f0870cba109e041f8af66866cf5b499478e654e7` |

`LICENSE.md` is the Database Tools copyright and Apache 2.0 notice.
`APACHE-2.0.txt` supplies the complete Apache license text.
`THIRD-PARTY-NOTICES` retains the notices for dependencies bundled with the tools.
