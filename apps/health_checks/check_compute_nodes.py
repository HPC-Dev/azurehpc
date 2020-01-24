#!/bin/env python3

import argparse
from subprocess import Popen, PIPE, check_output
import json
import os, sys
import time
import datetime
import logging

parser = argparse.ArgumentParser(prog='PROG', usage='%(prog)s [options]')
parser.add_argument("-q", type=str, help="PBS Queue to evaluate")
parser.add_argument("--debug", action="store_true", help="Print extra information to stdout")
parser.add_argument("--all_nodes", action="store_true", help="Check all nodes known to PBS")
parser.add_argument("--ib_tests", action="store_true", help="Run all of the IB tests")
parser.add_argument("--mem_bw_tests", action="store_true", help="Run a memory bandwidth test on all of the nodes")
parser.add_argument("--vm_type", type=str, default=None, help="Ex) --vm_type=hbv2")
args = parser.parse_args()

if args.debug:
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
else:
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)

logging.info("Queue: {}".format(args.q))
logging.info("All nodes: {}".format(args.all_nodes))
logging.info("IB tests: {}".format(args.ib_tests))
logging.info("Mem BW tests: {}".format(args.mem_bw_tests))
logging.info("VM type: {}".format(args.vm_type))

# Define the cut off values for different sku types 
if args.vm_type == None:
    cutoff_latency = 3.0
    bibw_value = 7000
elif args.vm_type.lower() == "hbv2":
    cutoff_latency = 1.8
    bibw_value = 15000

def run_latency_test(node1, node2, queue):
    qsub_cmd = "qsub -N osu_bw_test -joe -l select=1:ncpus=1:mem=10gb:host={}+1:ncpus=1:mem=10gb:host={} -l place=excl ~/apps/health_checks/run_ring_osu_bw_hpcx.pbs".format(node1, node2)
    cmd=qsub_cmd.split()
    if queue != None:
        cmd.insert(1,"-q")
        cmd.insert(2, queue)
    logging.debug("Qsub cmd: {}".format(" ".join(cmd)))
    output = check_output(" ".join(cmd), shell=True)
    output = output.decode().strip()
    logging.debug(output)

def wait_for_jobs_to_finish(check_text):
    # Wait for jobs to finish
    while True:
        output = check_output("/opt/pbs/bin/qstat -aw | grep {} | wc -l".format(check_text), shell=True)
        remaining_jobs = int(output)
        print("Remaining Jobs: {:05d}".format(remaining_jobs), end="\r")
        if remaining_jobs == 0:
            print()
            break
        time.sleep(5)

def check_nodes_for_ib_issues(check_lines, check_type, cutoff_value):
    # loop through the nodes to find nodes with poor latency
    hosts = {}
    for line in check_lines:
        tmp = line.split()
        if len(tmp) is 0:
            continue
        host1 = tmp[0].split("_")[0]
        host2 = tmp[0].split("_")[2]
        value = tmp[-1].strip()
        logging.debug("Host 1: {}, Host 2: {}, {} Value: {}".format(host1, host2, check_type, value))
        ib_results[host1] = {host2: {check_type: value}}

        check_results = False
        if check_type == "latency":
            check_results = float(value) > cutoff_value
        elif check_type == "bibw":
            check_results = float(value) < cutoff_value

        if check_results:
            if host1 not in hosts:
                hosts[host1] = 1
            else:
                hosts[host1] += 1
            if host2 not in hosts:
                hosts[host2] = 1
            else:
                hosts[host2] += 1

    return(hosts)
 

# Find all nodes
pbsnodes_cmd = "/opt/pbs/bin/pbsnodes -avS | grep free"
logging.debug("Find nodes cmd: {}".format(pbsnodes_cmd))
output = check_output(pbsnodes_cmd, shell=True)
output = output.decode().strip()
tmp = output.split("\n")
logging.debug("Output: {}".format(tmp))
node_dict = dict()
for line in tmp[2:]:
    if args.debug:
        logging.debug("Output: {}".format(line))
    if line == "":
        continue
    data = line.split()
    node_dict[data[0]] = {"state": data[1], "host": data[4], "queue": data[5]}

nodes = list(node_dict.keys())
num_of_nodes = len(nodes)
logging.debug("# of Nodes: {}".format(num_of_nodes))
logging.debug("Nodes: {}".format(nodes))
logging.debug("{}".format(node_dict))

# Make dir to store results
new_dir = os.path.join(os.getcwd(), datetime.datetime.now().strftime('Health_tests_%Y%m%d_%H%M%S'))
os.makedirs(new_dir)
os.chdir(new_dir)

suspect_hosts = {"latency": {}, "bandwidth": {}}
ib_results = dict()
offline_nodes = list()
recheck_nodes = list()
if args.ib_tests:
    logging.info("Run IB tests")
    for n_cnt, node in enumerate(nodes):
        node1=node_dict[node]["host"]
        if n_cnt+1 == num_of_nodes:
            node2 = node_dict[nodes[0]]["host"]
        else:
            node2 = node_dict[nodes[n_cnt+1]]["host"]
        logging.debug("Node1: {}, Node2: {}".format(node1, node2))
        run_latency_test(node1, node2, args.q)
        time.sleep(0.2)

    # Wait for ib jobs to complete
    wait_for_jobs_to_finish("osu_bw_test")

    # Process results
    output = check_output('grep -T "^8 " mem_bw_test.o** | sort -n -k 2', shell=True)
    output = output.decode()
    out_lines = output.split("\n")

    # Check IB for slow latency
    suspect_hosts["latency"] = check_nodes_for_ib_issues(out_lines, "latency", cutoff_latency)
    logging.info("Slow latency hosts: {}".format(suspect_hosts["latency"]))

    # Check to see if same host was involved in two slow runs
    for host in suspect_hosts["latency"]:
        if suspect_hosts["latency"][host] > 1:
            logging.warn("Offline host: {}".format(host))
            offline_nodes.append([host, "Slow latency"])
        else:
            logging.info("Run an additional test on host {} to check".format(host))
            recheck_nodes.append([host, "latency"])
    
logging.debug("Recheck nodes: {}".format(recheck_nodes))


if args.mem_bw_tests:
    logging.info("Run memory bw tests")
    nodes = list(node_dict.keys())
    for node in nodes:
        qsub_cmd = "qsub -N mem_bw_test -l select=1:ncpus=1:host={} -l place=excl -joe ~/apps/health_checks/run_mem_bw_test.sh".format(node_dict[node]["host"])
        output = check_output(qsub_cmd, shell=True)
        output = output.decode().strip()
        logging.debug(output)

    # Wait for the jobs to finish 
    wait_for_jobs_to_finish("mem_bw_test")

    # Process results
    output = check_output('grep -T "MB/s Failed" mem_bw_test.[o]* | sort -n -k 2', shell=True)
    output = output.decode()
    out_lines = output.split("\n")

    # Check for low memory bandwidth
    # loop through the nodes to find nodes with poor latency
    hosts = {}
    if len(out_lines) == 1 and out_lines[0] == "":
        logging.info("All {} nodes passed the memory bandwidth test".format(len(nodes)))
    else:
        for line in out_lines:
            tmp = line.split()
            if len(tmp) is 0:
                continue
            host = tmp[1].strip()
            value = tmp[3].strip()
            logging.warn("{} failed memory bandwidth test: {} MB/s".format(host, value))
            offline_nodes.append([host, "low memory bandwidth - {} MB/s".format(value)])

logging.info("Offline nodes: {}".format(offline_nodes))