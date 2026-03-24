# ERA5 10m wind input

Place daily ERA5 files here using this naming pattern:

`era5_YYYYMMDD.nc`

Expected variables:
- `u10` = 10m u-component of wind
- `v10` = 10m v-component of wind

The pipeline will use these files for true 10m wind when available. Otherwise it falls back to the internal proxy.
