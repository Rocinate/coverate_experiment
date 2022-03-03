# -*- coding: UTF-8 -*-
#!/usr/bin/env python
import sys
import os
import yaml
import numpy as np
import time
import math
import threading
from queue import Queue

# if python3
time.clock = time.time

# 添加路径
currentUrl = os.path.dirname(__file__)
parentUrl = os.path.abspath(os.path.join(currentUrl, os.pardir))
sys.path.append(parentUrl)

# 模拟参数 --local仅本地画图模拟
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--local", help="Run using local simulation.", action="store_true")
parser.add_argument("--record", help="save the waypoints.", action="store_true")
parser.add_argument("--load", help="load waypoints from record.", action="store_true")
args = parser.parse_args()

if not args.local:
    # 无人机接口
    from pycrazyswarm import *
    from CFController import CFController

# 自定义库
from borderdVoronoi import Vor
from cassingle import Cassingle
from graphController import Graph

# 读取无人机位置配置
# with open("online_simulation/crazyfiles.yaml", "r") as f:
with open("crazyfiles.yaml", "r") as f:
    data = yaml.load(f)
allCrazyFlies = data['files']

# 实验参数
STOP = False
numIterations = 40
xRange = 3.5
yRange = 2.5
box = np.array([-xRange, xRange, -yRange, yRange])  # 场地范围
lineSpeed = 0.1
angularSpeed = 0.2
draw =  True# 是否画图
T = 5.0
N = 10
allcfsTime = T/N
volume = 0.05
Z = 1.0 # 高度
threadNum = 4 # 线程数

class workers(threading.Thread):
    def __init__(self, q, name, cassingle):
        threading.Thread.__init__(self)
        self.q = q
        self.name = name
        self.cassingle = cassingle
        self.busy = False
        self.running = True
        self.res = None

    def run(self):
        print(self.name+" started!")
        while self.running:
            if not self.q.empty():
                try:
                    self.busy = True
                    self.res = None
                    flie, virtualResult = self.q.get(False)
                    self.res = vorProcess(flie, virtualResult, self.cassingle)
                    self.busy = False
                except Exception as e:
                    print("queue empty!", e)

    def terminate(self):
        self.running = False

def multiThreads(taskPool, cassingle):
    # 线程名称
    casadiLists = ["Thread"+str(i) for i in range(1, 1+threadNum)]
    # 储存列表
    threadList = []

    for threadName in casadiLists:
        thread = workers(taskPool, threadName, cassingle)
        thread.start()
        threadList.append(thread)

    return threadList

def vorProcess(flie, virtualResult, cassingle):
    waypoints = []
    print(flie['Id'])
    # 找出对应Id储存在allcrazyfiles中的索引
    [matchIndex] =  [index for (index, item) in enumerate(allCrazyFlies) if item['Id'] == flie['Id']]

    # 找到对应Id的虚拟维诺划分
    virtualFlie = [virtual for virtual in virtualResult if virtual['Id'] == flie['Id']][0]

    # casadi运算下一步位置
    outPut = cassingle.update(
        flie['vertices'],
        flie['centroid'],
        virtualFlie['vertices'],
        allCrazyFlies[matchIndex]['Position'],
        allCrazyFlies[matchIndex]['Pose']
    )

    allCrazyFlies[matchIndex]['Position'] = [pos for pos in outPut[-1][0:2]]
    allCrazyFlies[matchIndex]['Pose'] = round(outPut[-1][-1], 2)

    for timeIndex, item in enumerate(outPut):
        waypoints.append({
            'Id': allCrazyFlies[matchIndex]['Id'],
            'Px': item[0],
            'Py': item[1],
            'theta': item[2],
            'index': timeIndex
        })

    return np.array(outPut), allCrazyFlies[matchIndex]['Id'], waypoints

# 通过casadi计算得到结果
def getWaypoint():
    # 时间统计
    start = time.clock()

    vor = Vor(box, lineSpeed, angularSpeed)

    cassingle = Cassingle(lineSpeed, angularSpeed, T, N, xRange, yRange, volume, method="objective")

    if draw:
        graph = Graph([str(cf['Id']) for cf in allCrazyFlies], xRange, yRange)

    allWaypoints = []

    taskPool = Queue() # 根据参数创建队列

    threadList = multiThreads(taskPool, cassingle)

    print("start calculating!")

    for counter in range(numIterations):
        print("epoch: {}, progress: {}%".format(
            counter,
            round(float(counter)/numIterations * 100, 2)
        ))

        # 更新维诺划分，下面过程中需要真实的和虚拟的位置
        vorResult = vor.updateVor(allCrazyFlies)
        virtualResult = vor.virtualVor(allCrazyFlies)

        for flie in vorResult:
            taskPool.put((flie, virtualResult))

        # 等待所有的任务执行完毕
        while True:
            if all([not thread.busy for thread in threadList]):
                break

        waypoints = []
        # 将线程结果绘画出来
        for thread in threadList:
            draw and graph.updateTrack(
                    thread.res[0],
                    thread.res[1]
                )
            waypoints.append(thread.res[2])

        # 根据时间索引进行排序
        waypoints = sorted(waypoints, key = lambda i: i['index'])

        allWaypoints.append(waypoints)

        # 更新维诺质心
        draw and graph.updateCentroid(
            np.array([cf['centroid'] for cf in vorResult]) # 真实位置维诺质心
            # np.array([cf['centroid'] for cf in virtualResult])
        )

        # 使用虚拟位置更新维诺边界
        draw and graph.updateRidges(virtualResult)
        # draw and graph.updateRidges(vorResult)

    print("consume: {}s to go through casadi".format(time.clock() - start))

    # 关闭所有子线程
    for thread in threadList:
        thread.terminate()

    print("all children threads closed.")

    return allWaypoints

if __name__ == "__main__":
    allWaypoints = []

    # --load从本地文件直接读取路径结果
    if args.load:
        f = open("text.txt", "rb")
        allWaypoints = pickle.load(f)
        f.close()
    else:
        allwaypoints = getWaypoint()

    # --record, 记录路径结果到本地txt文件，方便直接读取
    if args.record:
        import pickle
        f = open("record.txt", "wb")
        pickle.dump(allWaypoints, f)
        f.close()

    if not args.local:
        cfController = CFController(allCrazyFlies, N, T, Z, lineSpeed)
        print("casadi down, execute all waypoints")

        cfController.startFlies()
        for waypoints in allWaypoints:
            # 实时飞行
            cfController.goWaypoints(waypoints)

        # 降落
        cfController.goLand()