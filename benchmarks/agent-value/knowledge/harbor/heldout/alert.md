# Overflow alert, current

Record: harbor-alert
Revision: 2
Status: active
Source: fictional project operations handbook

Revision 2 raises an overflow alert after two contiguous high-confidence samples at or above the configured threshold. Low-confidence or below-threshold samples reset the streak. Retain one alert for a continuing streak, and allow a new alert after a reset. Alert time is the second qualifying timestamp. Process observations in timestamp order and reject duplicate timestamps.
