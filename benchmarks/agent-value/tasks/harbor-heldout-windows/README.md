# Harbor fictional project slice

Python 3.12, standard library only. No service or provider dependency.
The public interface is api.handle(envelope), where envelope has state (object)
and commands (array). Results contains one object per command; state is the
resulting persistence snapshot. Commands run in order. An invalid command must
not partially commit. health returns {"ok":true} without mutation.
Integer fields exclude booleans. ValueError errors are stable error-code strings.

Files: api.py owns interface/transaction boundaries; domain.py owns the feature;
repository.py owns snapshots. Preserve all unrelated state and the health command.
Do not edit protected README.md, test_visible.py or visible.json. New implementation
helpers must be declared in the attempt input/output contract before use; the
initial editable files are api.py, domain.py and repository.py only.

Exact visible check command (no platform suite):
python -B -m unittest test_visible.Visible.test_health test_visible.Visible.test_contract

windows partitions [start,end) into width-minute half-open buckets, clipping the final bucket. Return start,end,count,sum for every bucket in ascending order, including empty ones. width must be a positive integer and end>start. Ignore samples outside the range.
