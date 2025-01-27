#!/usr/bin/env python3

# Import the necessary libraries
from tokenize import String
from urllib import request

from sympy import capture, re
import rospy  # Python library for ROS
from sensor_msgs.msg import Image  # Image is the message type
from std_msgs.msg import String  # String is the message type
# Package to convert between ROS and OpenCV Images
from cv_bridge import CvBridge, CvBridgeError
import cv2  # OpenCV library
from sensor_msgs.msg import Image
from metrics_refbox_msgs.msg import ObjectDetectionResult, Command
import rospkg
import os
from datetime import datetime

import controller_manager_msgs.srv
import rospy
import trajectory_msgs.msg

# importing Yolov5 model
from detect_modified import run


class object_detection():
    def __init__(self) -> None:
        rospy.loginfo("Object Detection node is ready...")
        self.cv_bridge = CvBridge()
        self.image_queue = None
        self.clip_size = 2  # manual number
        self.stop_sub_flag = False
        self.cnt = 0

        # yolo model config
        self.model_name = 'best.pt'
        self.confidence_threshold = 0.5

        # publisher
        self.output_bb_pub = rospy.Publisher(
            "/metrics_refbox_client/object_detection_result", ObjectDetectionResult, queue_size=10)

        # HSR pan motion publisher
        self.hsr_pan_pub = rospy.Publisher(
            '/hsrb/head_trajectory_controller/command',
            trajectory_msgs.msg.JointTrajectory, queue_size=10)
        self.move_right_flag = False
        self.move_left_flag = True

        # set of the HSR camera to get front straight view
        self.move_front_flag = False
        self._hsr_head_controller('front')

        # subscriber
        self.requested_object = None
        self.referee_command_sub = rospy.Subscriber(
            "/metrics_refbox_client/command", Command, self._referee_command_cb)

    def _input_image_cb(self, msg):
        """
        :msg: sensor_msgs.Image
        :returns: None

        """
        try:
            if not self.stop_sub_flag:

                # convert ros image to opencv image
                cv_image = self.cv_bridge.imgmsg_to_cv2(msg, "bgr8")
                if self.image_queue is None:
                    self.image_queue = []

                self.image_queue.append(cv_image)
                # print("Counter: ", len(self.image_queue))

                if len(self.image_queue) > self.clip_size:
                    # Clip size reached
                    # print("Clip size reached...")
                    rospy.loginfo("Image received..")

                    self.stop_sub_flag = True

                    # pop the first element
                    self.image_queue.pop(0)

                    # deregister subscriber
                    self.image_sub.unregister()

                    # call object inference method
                    output_prediction = self.object_inference()

        except CvBridgeError as e:
            rospy.logerr(
                "Could not convert ros sensor msgs Image to opencv Image.")
            rospy.logerr(str(e))
            self._check_failure()
            return

    def object_inference(self):

        rospy.loginfo("Object Inferencing Started...")

        opencv_img = self.image_queue[0]

        #####################
        # Load YOLOv5 model for inferencing
        #####################
        # get an instance of RosPack with the default search paths
        rospack = rospkg.RosPack()

        # get the file path for object_detection package
        pkg_path = rospack.get_path('object_detection')
        model_path = pkg_path + "/models/"
        data_config_path = pkg_path + "/scripts/"
        # Give the incoming image for inferencing
        predictions = run(weights=model_path + self.model_name,
                          data=data_config_path + "heartmet.yaml",
                          source=opencv_img)

        # extracting bounding boxes, labels, and scores from prediction output
        output_bb_ary = predictions['boxes']
        output_labels_ary = predictions['labels']
        output_scores_ary = predictions['scores']

        detected_object_list = []
        detected_object_score = []
        detected_bb_list = []

        # Extract required objects from prediction output
        print("---------------------------")
        print("Name of the objects, Score\n")
        for idx, value in enumerate(output_labels_ary):
            object_name = value
            score = output_scores_ary[idx]

            if score > self.confidence_threshold:
                detected_object_list.append(object_name)
                detected_object_score.append(score)
                detected_bb_list.append(output_bb_ary[idx])

                print("{}, {}".format(object_name, score))

        print("---------------------------")

        # Only publish the target object requested by the referee
        if (self.requested_object).lower() in detected_object_list:
            rospy.loginfo("--------> Object detected <--------")
            requested_object_string = (self.requested_object).lower()
            object_idx = detected_object_list.index(requested_object_string)
            print("---------------------------")
            print("Bounding Box: ", detected_bb_list)
            print("---------------------------")
            # Referee output message publishing
            object_detection_msg = ObjectDetectionResult()
            object_detection_msg.message_type = ObjectDetectionResult.RESULT
            object_detection_msg.result_type = ObjectDetectionResult.BOUNDING_BOX_2D
            object_detection_msg.object_found = True
            object_detection_msg.box2d.min_x = int(
                detected_bb_list[object_idx][0])
            object_detection_msg.box2d.min_y = int(
                detected_bb_list[object_idx][1])
            object_detection_msg.box2d.max_x = int(
                detected_bb_list[object_idx][2])
            object_detection_msg.box2d.max_y = int(
                detected_bb_list[object_idx][3])

            # convert OpenCV image to ROS image message
            ros_image = self.cv_bridge.cv2_to_imgmsg(
                self.image_queue[0], encoding="passthrough")
            object_detection_msg.image = ros_image

            # publish message
            rospy.loginfo("Publishing result to referee...")
            self.output_bb_pub.publish(object_detection_msg)

            # draw bounding box on target detected object
            # opencv_img = cv2.rectangle(opencv_img, (int(detected_bb_list[object_idx][0]),
            #                                         int(detected_bb_list[object_idx][1])),
            #                            (int(detected_bb_list[object_idx][2]),
            #                             int(detected_bb_list[object_idx][3])),
            #                            (255, 255, 255), 2)

            # display image
            # cv2.imshow('Output Img', opencv_img)
            # cv2.waitKey(0)
            # cv2.destroyAllWindows()

            # ready for next image
            self.stop_sub_flag = False
            self.image_queue = []

            # initialize HSR motion flags
            self.move_right_flag = False
            self.move_left_flag = True

            # go back to center
            if not self.move_front_flag:
                self._hsr_head_controller('front')

        # requested object not detected
        else:
            rospy.loginfo("xxxxx > Object NOT FOUND < xxxxx")

            # TODO: check left and right images for object

            # move head in right direction
            if not self.move_right_flag:
                self.move_right_flag = True
                self.move_left_flag = False

                # ready for next image
                self.stop_sub_flag = False
                self.image_queue = []

                # move head to right
                # rospy.loginfo("Moving head to right...")
                self._hsr_head_controller('right')

            # move head in left direction
            elif not self.move_left_flag:
                self.move_right_flag = True
                self.move_left_flag = True

                # ready for next image
                self.stop_sub_flag = False
                self.image_queue = []

                # move head to left
                # rospy.loginfo("Moving head to left...")
                self._hsr_head_controller('left')

            elif self.move_left_flag and self.move_right_flag:

                rospy.loginfo("xxxxx > Object NOT FOUND < xxxxx")

                # Referee output message publishing
                object_detection_msg = ObjectDetectionResult()
                object_detection_msg.message_type = ObjectDetectionResult.RESULT
                object_detection_msg.result_type = ObjectDetectionResult.BOUNDING_BOX_2D
                object_detection_msg.object_found = False

                # convert OpenCV image to ROS image message
                ros_image = self.cv_bridge.cv2_to_imgmsg(
                    self.image_queue[0], encoding="passthrough")
                object_detection_msg.image = ros_image

                # publish message
                self.output_bb_pub.publish(object_detection_msg)

                # ready for next image
                self.stop_sub_flag = False
                self.image_queue = []
                self.move_right_flag = False
                self.move_left_flag = True

                # go back to center
                self._hsr_head_controller('front')

        # draw bounding box on all detected objects (with score >0.5)
        # for i in detected_bb_list:
        #     opencv_img = cv2.rectangle(opencv_img, (i[0], i[1]), (i[2], i[3]), (255,255,255), 2)

        # display image
        # cv2.imshow('Output Img', opencv_img)
        # cv2.waitKey(0)
        # cv2.destroyAllWindows()

        return predictions

    def _referee_command_cb(self, msg):

        # Referee comaand message (example)
        '''
        task: 1
        command: 1
        task_config: "{\"Target object\": \"Cup\"}"
        uid: "0888bd42-a3dc-4495-9247-69a804a64bee"
        '''

        # START command from referee
        if msg.task == 1 and msg.command == 1:

            print("\nStart command received")

            # set of the HSR camera to get front straight view
            if not self.move_front_flag:
                self._hsr_head_controller('front')

            # start subscriber for image topic
            self.image_sub = rospy.Subscriber("/hsrb/head_rgbd_sensor/rgb/image_raw",
                                              Image,
                                              self._input_image_cb)

            # extract target object from task_config
            self.requested_object = msg.task_config.split(":")[
                1].split("\"")[1]
            print("\n")
            print("Requested object: ", self.requested_object)
            print("\n")

        # STOP command from referee
        if msg.command == 2:

            self.image_sub.unregister()
            self.stop_sub_flag = False
            rospy.loginfo("Received stopped command from referee")
            rospy.loginfo("Subscriber stopped")

    def _hsr_head_controller(self, head_direction):
        '''
        This function is used to control the head of the robot.
        '''
        # wait to establish connection between the controller
        while self.hsr_pan_pub.get_num_connections() == 0:
            rospy.sleep(0.1)

        # make sure the controller is running
        rospy.wait_for_service('/hsrb/controller_manager/list_controllers')
        list_controllers = rospy.ServiceProxy(
            '/hsrb/controller_manager/list_controllers',
            controller_manager_msgs.srv.ListControllers)
        running = False
        while running is False:
            rospy.sleep(0.1)
            for c in list_controllers().controller:
                if c.name == 'head_trajectory_controller' and c.state == 'running':
                    running = True

        # set the target position of the head
        traj = trajectory_msgs.msg.JointTrajectory()
        traj.joint_names = ["head_pan_joint", "head_tilt_joint"]
        p = trajectory_msgs.msg.JointTrajectoryPoint()

        # pan motion range: -3.839 to 1.745 (rad) | -220 to 100 (deg)
        # tilt motion range: -1.570 to 0.523 (rad) | -90 to 30 (deg)
        # +Ve pos value means anti-clockwise rotation
        # motion (pan, tilt)
        # total field of view is -60 to 60 cm = 120cm (in x-axis)

        if head_direction == 'front':

            rospy.loginfo("Moving head to get front view...")

            # move head to right
            p.positions = [0.0, -0.5]
            p.velocities = [0.0, 0.0]
            p.time_from_start = rospy.Duration(2)
            traj.points = [p]
            self.hsr_pan_pub.publish(traj)

            # wait for the head to finish moving
            rospy.sleep(3)

            if not self.move_front_flag:
                self.move_front_flag = True

            ###########################################
            # TODO: check/read current angle of the head
            ###########################################

            # waiting for referee box to be ready
            rospy.loginfo("Waiting for referee box ...")

            # start subscriber for image topic
            # self.image_sub = rospy.Subscriber("/hsrb/head_rgbd_sensor/rgb/image_raw",
            #                                     Image,
            #                                     self._input_image_cb)

        elif head_direction == 'right':

            rospy.loginfo("Moving head to right...")

            # move head to right
            p.positions = [-0.2, -0.5]
            p.velocities = [0.0, 0.0]
            p.time_from_start = rospy.Duration(1)
            traj.points = [p]
            self.hsr_pan_pub.publish(traj)

            # wait for the head to finish moving
            rospy.sleep(2)

            self.move_front_flag = False

            # start subscriber for image topic
            self.image_sub = rospy.Subscriber("/hsrb/head_rgbd_sensor/rgb/image_raw",
                                              Image,
                                              self._input_image_cb)

        elif head_direction == 'left':

            rospy.loginfo("Moving head to left...")

            # move head to left
            p.positions = [0.2, -0.5]
            p.velocities = [0.0, 0.0]
            p.time_from_start = rospy.Duration(1)
            traj.points = [p]
            self.hsr_pan_pub.publish(traj)

            # wait for the head to finish moving
            rospy.sleep(2)

            self.move_front_flag = False

            # start subscriber for image topic
            self.image_sub = rospy.Subscriber("/hsrb/head_rgbd_sensor/rgb/image_raw",
                                              Image,
                                              self._input_image_cb)


if __name__ == "__main__":
    rospy.init_node("object_detection_node")
    object_detection_obj = object_detection()

    rospy.spin()
