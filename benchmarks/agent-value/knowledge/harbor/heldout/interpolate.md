# Short-gap estimates

Record: harbor-interpolate
Revision: 1
Status: active
Source: fictional project operations handbook

Only interior missing samples bounded by measured values may be interpolated. Use linear interpolation at the actual timestamps, with exact rational arithmetic and round to three decimal places, ties-to-even. A gap whose endpoints are more than 20 minutes apart stays missing. Leading/trailing gaps stay missing. Each output marks measured, estimated or missing quality.
