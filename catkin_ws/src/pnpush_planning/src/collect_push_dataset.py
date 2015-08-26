#!/usr/bin/env python

# Peter KT Yu, Aug 2015
# automate the process of pushing against an object and record the data

import sys
import numpy as np
from ik.roshelper import ROS_Wait_For_Msg
from ik.roshelper import lookupTransform
from ik.roshelper import coordinateFrameTransform
from ik.helper import Timer
from ik.ik import setSpeed
from geometry_msgs.msg import WrenchStamped
import tf
import tf.transformations as tfm
import rospy
import json
import roslib; roslib.load_manifest("robot_comm")
from robot_comm.srv import *
roslib.load_manifest("netft_rdt_driver")
from netft_rdt_driver.srv import Zero
import sensor_msgs.msg
import geometry_msgs.msg
import os
import scipy.io as sio
from visualization_msgs.msg import Marker
from marker_helper import createMeshMarker
from marker_helper import createPointMarker
from marker_helper import createArrowMarker
from marker_helper import createSphereMarker
from tf.broadcaster import TransformBroadcaster
from math import pi
import pdb
import copy
import subprocess, os, signal
from shape_db import ShapeDB

setCartRos = rospy.ServiceProxy('/robot2_SetCartesian', robot_SetCartesian)
setZero = rospy.ServiceProxy('/zero', Zero)
setZone = rospy.ServiceProxy('/robot2_SetZone', robot_SetZone)

def setCart(pos, ori):
    param = (np.array(pos) * 1000).tolist() + ori
    #print 'setCart', param
    #pause()
    setCartRos(*param)

def pause():
    print 'Press any key to continue'
    raw_input()

def norm(vect):
    vect = np.array(vect)
    return np.sqrt(np.dot(vect, vect))

# need to be removed
def vizBlock(pose):
    # prepare block visualization
    global vizpub
    meshmarker = createMeshMarker('package://pnpush_config/models/object_meshes/SteelBlock.stl', 
                              offset=tuple(pose[0:3]), rgba=(0.5,0.5,0.5,1),
                              orientation=tuple(pose[3:7]), frame_id='vicon/SteelBlock/SteelBlock')
    vizpub.publish(meshmarker)
    rospy.sleep(0.05)
    
def poselist2mat(pose):
    return np.dot(tfm.translation_matrix(pose[0:3]), tfm.quaternion_matrix(pose[3:7]))

def mat2poselist(mat):
    pos = tfm.translation_from_matrix(mat)
    quat = tfm.quaternion_from_matrix(mat)
    return pos.tolist() + quat.tolist()

def wait_for_ft_calib():
    ROS_Wait_For_Msg('/netft_data', geometry_msgs.msg.WrenchStamped).getmsg()

import os
import errno

def make_sure_path_exists(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

def main(argv):
    # prepare the proxy, listener
    global listener
    global vizpub
    rospy.init_node('contour_follow', anonymous=True)
    listener = tf.TransformListener()
    vizpub = rospy.Publisher("visualization_marker", Marker, queue_size=10)
    br = TransformBroadcaster()
    
    parser = optparse.OptionParser()
    parser.add_option('-s', action="store", dest='shape_id', 
                      help='The shape id e.g. rect1-rect3', default='rect1')
    
    # set the parameters
    globalvel = 200  # speed for moving around
    ori = [0, 0.7071, 0.7071, 0]
    z = 0.218   # the height above the table probe1: 0.29 probe2: 0.218
    zup = z + 0.05
    probe_radius = 0.004745   # probe1: 0.00626/2 probe2: 0.004745
    dist_before_contact = 0.02 
    dist_after_contact = 0.05

    # space for the experiment
    speeds = [20, 50, 100, 200, 400]
    side_params = np.linspace(0.1,0.9,3)
    angles = np.linspace(-pi/4, pi/4, 3)

    global_frame_id = '/map'
    
    # parameters about object
    shape_db = ShapeDB()
    shape_polygon = shape_db.shape_db[shape_id]['shape_poly'] # shape of the objects presented as polygon.
    obj_frame_id = shape_db.shape_db[shape_id]['frame_id']

    # parameters about rosbag
    dir_save_bagfile = os.environ['PNPUSHDATA_BASE'] + '/push_dataset_motion/'
    topics = ['/joint_states', '/netft_data', '/tf', '/visualization_marker']
    
    setSpeed(tcp=globalvel, ori=1000)
    setZone(0)
    make_sure_path_exists(dir_save_bagfile)
    
    # enumerate the speed
    for v in speeds:
        # enumerate the side we want to push
        for i in range(len(shape_polygon)):
            
            # enumerate the contact point that we want to push
            for s in side_params:
                pos = np.array(shape_polygon[i]) *s + np.array(shape_polygon[(i+1) % len(shape_polygon)]) *(1-s)
                tangent = np.array(shape_polygon[(i+1) % len(shape_polygon)]) - np.array(shape_polygon[i])
                normal = np.array([tangent[1], -tangent[0], 0]) 
                normal = normal / norm(normal)  # normalize it
                normal = np.append(normal, [0])
                
                # enumerate the direction in which we want to push
                for t in angles:
                    bagfilename = 'push_shape=%s_v=%.0f_i=%.3f_s=%.3f_t=%.3f.bag' % (shape_id, v, i, s, t)
                    bagfilepath = dir_save_bagfile+bagfilename
                    # if exists then skip it
                    if os.path.isfile(bagfilepath):
                        print bagfilepath, 'exits', 'skip'
                        continue  
                    # find the probe pos in contact in object frame
                    pos_probe_contact_object = pos + normal * probe_radius
                    # find the start point
                    direc = np.dot(tfm.euler_matrix(0,0,t) , normal.tolist() + [1])[0:3] # in the direction of moving out
                    pos_start_probe_object = pos_probe_contact_object + direc * dist_before_contact
                    # find the end point
                    pos_end_probe_object = pos_probe_contact_object - direc * dist_after_contact
                    
                    # zero force torque sensor
                    rospy.sleep(0.1)
                    setZero()
                    wait_for_ft_calib()
                    
                    # transform start and end to world frame
                    pos_start_probe_world = coordinateFrameTransform(pos_start_probe_object, obj_frame_id, global_frame_id, listener)
                    pos_end_probe_world = coordinateFrameTransform(pos_end_probe_object, obj_frame_id, global_frame_id, listener)

                    # start bag recording
                    # move to startPos
                    start_pos = copy.deepcopy(pos_start_probe_world)
                    start_pos[2] = zup
                    setCart(start_pos,ori)
        
                    start_pos = copy.deepcopy(pos_start_probe_world)
                    start_pos[2] = z
                    setCart(start_pos,ori)
                    
                    rosbag_proc = subprocess.Popen('rosbag record -q -O %s %s' % (bagfilename, " ".join(topics)) , shell=True, cwd=dir_save_bagfile)
                    print 'rosbag_proc.pid=', rosbag_proc.pid
                    rospy.sleep(0.1)
                    
                    end_pos = copy.deepcopy(pos_end_probe_world)
                    end_pos[2] = z
                    setSpeed(tcp=v, ori=1000)
                    setCart(end_pos,ori)
                    setSpeed(tcp=globalvel, ori=1000)
                    
                    # end bag recording
                    terminate_ros_node("/record")
                    
                    # move up vertically
                    end_pos = copy.deepcopy(pos_end_probe_world)
                    end_pos[2] = zup
                    setCart(end_pos,ori)
                    

    # move back to startPos
    start_pos = [0.2, 0, z + 0.05]
    setCart(start_pos,ori)

def terminate_ros_node(s):
    list_cmd = subprocess.Popen("rosnode list", shell=True, stdout=subprocess.PIPE)
    list_output = list_cmd.stdout.read()
    retcode = list_cmd.wait()
    assert retcode == 0, "List command returned %d" % retcode
    for str in list_output.split("\n"):
        if (str.startswith(s)):
            os.system("rosnode kill " + str)


if __name__=='__main__':
    main(sys.argv)


#rosservice call /robot2_SetSpeed 10 1
#rosservice call /robot2_SetZone "mode: 1"



