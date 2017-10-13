mkdir pkg
mkdir pkg\Configuration
mkdir pkg\DataModel
mkdir "pkg\Resource Drivers - Python"
mkdir "pkg\Resource Scripts"
mkdir "pkg\Topology Scripts"

copy vMX_Package\metadata.xml                                                                     pkg
copy vMX_Package\Configuration\shellconfig.xml                                                    pkg\Configuration
copy vMX_Package\DataModel\datamodel.xml                                                          pkg\DataModel

cd "vMX_Package\Resource Drivers - Python\vMX VNF Deployment Resource Driver"
set fn="..\..\..\pkg\Resource Drivers - Python\vMX VNF Deployment Resource Driver.zip"
"c:\Program Files\7-Zip\7z.exe" a %fn% *
cd ..\..\..

cd "vMX_Package\Resource Drivers - Python\VNF Connectivity Manager L2 Driver"
set fn="..\..\..\pkg\Resource Drivers - Python\VNF Connectivity Manager L2 Driver.zip"
"c:\Program Files\7-Zip\7z.exe" a %fn% *
cd ..\..\..


cd "vMX_Package\Topology Scripts\hook_setup"
set fn="..\..\..\pkg\Topology Scripts\hook_setup.zip"
"c:\Program Files\7-Zip\7z.exe" a %fn% *
cd ..\..\..

cd "vMX_Package\Topology Scripts\hook_teardown"
set fn="..\..\..\pkg\Topology Scripts\hook_teardown.zip"
"c:\Program Files\7-Zip\7z.exe" a %fn% *
cd ..\..\..


cd "vMX_Package\Resource Scripts\vnf_cleanup_orch_hook_post_teardown"
set fn="..\..\..\pkg\Resource Scripts\vnf_cleanup_orch_hook_post_teardown.zip"
"c:\Program Files\7-Zip\7z.exe" a %fn% *
cd ..\..\..



cd pkg
set fn="..\vMX_Package.zip"
del %fn%
"c:\Program Files\7-Zip\7z.exe" a %fn% *
cd ..

rmdir /s /q pkg
