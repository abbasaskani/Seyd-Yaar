Local ocean-data override for Seyd-Yaar

Optional: place NetCDF files here and set path_template in backend/config/datasets.json.

Supported placeholders:
- {date_yyyymmdd}
- {date_yyyymmddhh}

Expected variables by default:
- sst: thetao
- currents: uo, vo
- ssh: zos
- waves: VHM0
- chl: chl
- sss: so
- o2: o2
- mld: mlotst
- npp: nppv

Expected coordinate names (any one of each):
- time or valid_time
- latitude / lat / nav_lat
- longitude / lon / nav_lon
- optional depth / deptht / depthu / depthv / olevel / lev

If path_template is empty or a file is missing, the pipeline tries Copernicus next.
