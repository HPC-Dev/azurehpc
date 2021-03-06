#!/bin/bash

APP_NAME=wrf
APP_VERSION=4.1.3
SKU_TYPE=${SKU_TYPE:-hb}
SHARED_APP=${SHARED_APP:-/apps}
MODULE_DIR=${SHARED_APP}/modulefiles/${SKU_TYPE}/${APP_NAME}
MODULE_NAME=${APP_VERSION}-openmpi
APP_DIR=$SHARED_APP/${SKU_TYPE}/${APP_NAME}-openmpi
APPS_WRF_DIR=`pwd`

function create_modulefile {
mkdir -p ${MODULE_DIR}
cat << EOF >> ${MODULE_DIR}/${MODULE_NAME}
#%Module
set              wrfversion        ${APP_VERSION}
set              WRFROOT           ${APP_DIR}/WRF-\$wrfversion
setenv           WRFROOT           ${APP_DIR}/WRF-\$wrfversion

append-path      PATH              \$WRFROOT/main
EOF
}

sudo yum install -y jasper-devel
sudo yum install -y libpng-devel

spack install  netcdf-fortran+mpi ^netcdf~parallel-netcdf ^hdf5+fortran %gcc@9.2.0 ^openmpi@4.0.2
source ${SPACK_ROOT}/share/spack/setup-env.sh

mkdir -p ${APP_DIR}
cd ${APP_DIR}
wget https://github.com/wrf-model/WRF/archive/v${APP_VERSION}.tar.gz
tar xvf v${APP_VERSION}.tar.gz

spack load netcdf-fortran^openmpi
spack load netcdf^openmpi
spack load hdf5^openmpi
spack load perl
module load mpi/openmpi-4.0.2
module load gcc-9.2.0

export HDF5=$(spack location -i hdf5^openmpi)
export NETCDF=$(spack location -i netcdf-fortran^openmpi)

NETCDF_C=$(spack location -i netcdf^openmpi)
ln -sf $NETCDF_C/include/* $NETCDF/include/
ln -sf $NETCDF_C/lib/* $NETCDF/lib/
ln -sf $NETCDF_C/lib/pkgconfig/* $NETCDF/lib/pkgconfig

cd WRF-${APP_VERSION}
patch -p0 < ${APPS_WRF_DIR}/WRFV4.0-rsl-8digit.patch

./configure << EOF
34

EOF

./compile -j 16 em_real

create_modulefile
