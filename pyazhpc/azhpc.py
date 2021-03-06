import argparse
import datetime
import json
import logging
import os
import shutil
import sys
import textwrap
import time

import arm
import azconfig
import azinstall
import azutil

from cryptography.hazmat.primitives import serialization as crypto_serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend as crypto_default_backend

log = logging.getLogger(__name__)

def do_preprocess(args):
    log.debug("reading config file ({})".format(args.config_file))
    config = azconfig.ConfigFile()
    config.open(args.config_file)
    print(json.dumps(config.preprocess(), indent=4))

def do_get(args):
    config = azconfig.ConfigFile()
    config.open(args.config_file)
    val = config.read_value(args.path)
    print(f"{args.path} = {val}")

def __add_unset_vars(vset, config_file):
    log.debug(f"looking for vars in {config_file}")
    config = azconfig.ConfigFile()
    config.open(config_file)
    vset.update(config.get_unset_vars())

def do_init(args):
    if not os.path.exists(args.config_file):
        log.error("config file/dir does not exist")
        sys.exit(1)

    if args.show:
        vlist = set()

        if os.path.isfile(args.config_file):
            __add_unset_vars(vlist, args.config_file)
        else:
            for root, dirs, files in os.walk(args.config_file):
                for name in files:
                    if os.path.splitext(name)[1] == ".json":
                        __add_unset_vars(vlist, os.path.join(root, name))

        print("Variables to set: " + ",".join(vlist))
        print()
        print("Example string for '--vars' argument (add values):")
        print("    --vars " + ",".join([ x+"=" for x in vlist ]))
    else:
        log.debug("creating directory")
        os.makedirs(args.dir, exist_ok=True)

        if os.path.isfile(args.config_file):
            shutil.copy(args.config_file, args.dir)
        elif os.path.isdir(args.config_file):
            for root, dirs, files in os.walk(args.config_file):
                for d in dirs:
                    newdir = os.path.join(
                        args.dir,
                        os.path.relpath(
                            os.path.join(root, d),
                            args.config_file
                        )
                    )
                    log.debug("creating directory: " + newdir)
                    os.makedirs(newdir, exist_ok=True)
                for f in files:
                    oldfile = os.path.join(root, f)
                    newfile = os.path.join(
                        args.dir,
                        os.path.relpath(
                            os.path.join(root, f),
                            args.config_file
                        )
                    )
                    log.debug(f"copying file: {oldfile} -> {newfile}")
                    shutil.copy(oldfile, newfile)

        # get vars
        vset = {}
        if args.vars:
            for vp in args.vars.split(","):
                vk, vv = vp.split("=")
                vset[vk] = vv
            
            for root, dirs, files in os.walk(args.dir):
                for name in files:
                    if os.path.splitext(name)[1] == ".json":
                        config = azconfig.ConfigFile()
                        config.open(os.path.join(root, name))
                        config.replace_vars(vset)
                        config.save(os.path.join(root, name))

def do_scp(args):
    log.debug("reading config file ({})".format(args.config_file))
    c = azconfig.ConfigFile()
    c.open(args.config_file)
    
    adminuser = c.read_value("admin_user")
    sshkey="{}_id_rsa".format(adminuser)
    # TODO: check ssh key exists

    jumpbox = c.read_value("install_from")
    rg = c.read_value("resource_group")
    fqdn = azutil.get_fqdn(rg, jumpbox+"pip")

    if args.args and args.args[0] == "--":
        scp_args = args.args[1:]
    else:
        scp_args = args.args

    scp_exe = "scp"
    scp_cmd = [
            scp_exe, "-q",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-i", sshkey,
            "-o", f"ProxyCommand=ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i {sshkey} -W %h:%p {adminuser}@{fqdn}"
        ] + scp_args
    log.debug(" ".join([ f"'{a}'" for a in scp_cmd ]))
    os.execvp(scp_exe, scp_cmd)

def do_connect(args):
    log.debug("reading config file ({})".format(args.config_file))
    c = azconfig.ConfigFile()
    c.open(args.config_file)
    
    adminuser = c.read_value("admin_user")
    ssh_private_key="{}_id_rsa".format(adminuser)
    # TODO: check ssh key exists
    
    if args.user == None:
        sshuser = adminuser
    else:
        sshuser = args.user

    jumpbox = c.read_value("install_from")
    resource_group = c.read_value("resource_group")
    fqdn = azutil.get_fqdn(resource_group, jumpbox+"pip")

    if fqdn == "":
        log.warning(f"The install node does not have a public IP - trying hostname ({jumpbox})")
 
    log.debug("Getting resource name")

    rtype = c.read_value(f"resources.{args.resource}.type", "hostname")

    target = args.resource

    if rtype == "vm":
        instances = c.read_value(f"resources.{args.resource}.instances", 1)
        
        if instances > 1:
            target = f"{args.resource}{1:04}"
            log.info(f"Multiple instances of {args.resource}, connecting to {target}")
    
    elif rtype == "vmss":
        vmssnodes = azutil.get_vmss_instances(resource_group, args.resource)
        if len(vmssnodes) == 0:
            log.error("There are no instances in the vmss")
            sys.exit(1)
        target = vmssnodes[0]
        if len(vmssnodes) > 1:
            log.info(f"Multiple instances of {args.resource}, connecting to {target}")

    elif rtype == "hostname":
        pass

    else:
        log.debug(f"Unknown resource type - {rtype}")
        sys.exit(1)

    ssh_exe = "ssh"
    cmdline = []
    if len(args.args) > 0:
        cmdline.append(" ".join(args.args))

    if args.resource == jumpbox:
        log.info("logging directly into {}".format(fqdn))
        ssh_args = [
            "ssh", "-t", "-q", 
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-i", ssh_private_key,
            f"{sshuser}@{fqdn}"
        ]
        log.debug(" ".join(ssh_args + cmdline))
        os.execvp(ssh_exe, ssh_args + cmdline)
    else:
        log.info("logging in to {} (via {})".format(target, fqdn))
        ssh_args = [
            ssh_exe, "-t", "-q",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-i", ssh_private_key,
            "-o", f"ProxyCommand=ssh -i {ssh_private_key} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -W %h:%p {sshuser}@{fqdn}",
            f"{sshuser}@{target}"
        ]
        log.debug(" ".join(ssh_args + cmdline))
        os.execvp(ssh_exe, ssh_args + cmdline)

def _exec_command(fqdn, sshuser, sshkey, cmdline):
    ssh_exe = "ssh"
    ssh_args = [
        ssh_exe, "-q", 
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-i", sshkey,
        f"{sshuser}@{fqdn}"
    ]
    log.debug(" ".join(ssh_args + [ cmdline ]))
    os.execvp(ssh_exe, ssh_args + [ cmdline ])

def do_status(args):
    log.debug("reading config file ({})".format(args.config_file))
    c = azconfig.ConfigFile()
    c.open(args.config_file)
    
    adminuser = c.read_value("admin_user")
    ssh_private_key="{}_id_rsa".format(adminuser)

    jumpbox = c.read_value("install_from")
    resource_group = c.read_value("resource_group")
    fqdn = azutil.get_fqdn(resource_group, jumpbox+"pip")

    if fqdn == "":
        log.warning("The install node does not have a public IP - trying hostname ({})".format(jumpbox))

    tmpdir = "azhpc_install_" + os.path.basename(args.config_file).strip(".json")
    _exec_command(fqdn, adminuser, ssh_private_key, f"pssh -h {tmpdir}/hostlists/linux -i -t 0 'printf \"%-20s%s\n\" \"$(hostname)\" \"$(uptime)\"' | grep -v SUCCESS")


def do_run(args):
    log.debug("reading config file ({})".format(args.config_file))
    c = azconfig.ConfigFile()
    c.open(args.config_file)
    
    adminuser = c.read_value("admin_user")
    ssh_private_key="{}_id_rsa".format(adminuser)
    # TODO: check ssh key exists
    
    if args.user == None:
        sshuser = adminuser
    else:
        sshuser = args.user

    jumpbox = c.read_value("install_from")
    resource_group = c.read_value("resource_group")
    fqdn = azutil.get_fqdn(resource_group, jumpbox+"pip")

    if fqdn == "":
        log.warning("The install node does not have a public IP - trying hostname ({})".format(jumpbox))

    hosts = []
    if args.nodes:
        for r in args.nodes.split(" "):
            rtype = c.read_value(f"resources.{r}.type", None)
            if not rtype:
                log.error(f"resource {r} does not exist in config")
                sys.exit(1)
            if rtype == "vm":
                instances = c.read_value(f"resources.{r}.instances", 1)
                if instances == 1:
                    hosts.append(r)
                else:
                    hosts += [ f"{r}{n:04}" for n in range(1, instances+1) ]            
            elif rtype == "vmss":
                hosts += azutil.get_vmss_instances(c.read_value("resource_group"), r)
        
    if not hosts:
        hosts.append(jumpbox)

    hostlist = " ".join(hosts)
    cmd = " ".join(args.args)
    _exec_command(fqdn, sshuser, ssh_private_key, f"pssh -H '{hostlist}' -i -t 0 '{cmd}'")

def do_build(args):
    log.debug(f"reading config file ({args.config_file})")
    tmpdir = "azhpc_install_" + os.path.basename(args.config_file).strip(".json")
    log.debug(f"tmpdir = {tmpdir}")
    if os.path.isdir(tmpdir):
        log.debug("removing existing tmp directory")
        shutil.rmtree(tmpdir)

    c = azconfig.ConfigFile()
    c.open(args.config_file)
    config = c.preprocess()

    adminuser = config["admin_user"]
    private_key_file = adminuser+"_id_rsa"
    public_key_file = adminuser+"_id_rsa.pub"
    if not (os.path.exists(private_key_file) and os.path.exists(public_key_file)):
        # create ssh keys
        key = rsa.generate_private_key(
            backend=crypto_default_backend(),
            public_exponent=65537,
            key_size=2048
        )
        private_key = key.private_bytes(
            crypto_serialization.Encoding.PEM,
            crypto_serialization.PrivateFormat.TraditionalOpenSSL,
            crypto_serialization.NoEncryption())
        public_key = key.public_key().public_bytes(
            crypto_serialization.Encoding.OpenSSH,
            crypto_serialization.PublicFormat.OpenSSH
        )
        with open(private_key_file, "wb") as f:
            os.chmod(private_key_file, 0o600)
            f.write(private_key)
        with open(public_key_file, "wb") as f:
            os.chmod(public_key_file, 0o644)
            f.write(public_key+b'\n')

    tpl = arm.ArmTemplate()
    tpl.read(config)

    log.info("writing out arm template to " + args.output_template)
    with open(args.output_template, "w") as f:
        f.write(tpl.to_json())

    log.info("creating resource group " + config["resource_group"])

    resource_tags = config.get("resource_tags", {})
    azutil.create_resource_group(
        config["resource_group"],
        config["location"],
        [
            {
                "key": "CreatedBy",
                "value": os.getenv("USER")
            },
            {
                "key": "CreatedOn",
                "value": datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            }
        ] + [ { "key": key, "value": resource_tags[key] } for key in resource_tags.keys() ]
    )
    log.info("deploying arm template")
    deployname = azutil.deploy(
        config["resource_group"],
        args.output_template
    )
    log.debug(f"deployment name: {deployname}")

    building = True
    success = True
    del_lines = 1
    while building:
        time.sleep(5)
        res = azutil.get_deployment_status(config["resource_group"], deployname)
        log.debug(res)
        
        print("\033[F"*del_lines)
        del_lines = 1

        for i in res:
            props = i["properties"]
            status_code = props["statusCode"]
            if props.get("targetResource", None):
                resource_name = props["targetResource"]["resourceName"]
                resource_type = props["targetResource"]["resourceType"]
                del_lines += 1
                print(f"{resource_name:15} {resource_type:47} {status_code:15}")
            else:
                provisioning_state = props["provisioningState"]
                del_lines += 1
                building = False
                if provisioning_state != "Succeeded":
                    success = False

    if success:
        log.info("Provising succeeded")
    else:
        log.error("Provisioning failed")
        for i in res:
            props = i["properties"]
            status_code = props["statusCode"]
            if props.get("targetResource", None):
                resource_name = props["targetResource"]["resourceName"]
                if props.get("statusMessage", None):
                    if "error" in props["statusMessage"]:
                        error_code = props["statusMessage"]["error"]["code"]
                        error_message = textwrap.TextWrapper(width=60).wrap(text=props["statusMessage"]["error"]["message"])
                        error_target = props["statusMessage"]["error"].get("target", None)
                        error_target_str = ""
                        if error_target:
                            error_target_str = f"({error_target})"
                        print(f"  Resource : {resource_name} - {error_code} {error_target_str}")
                        print(f"  Message  : {error_message[0]}")
                        for line in error_message[1:]:
                            print(f"             {line}")
        sys.exit(1)
    
    log.info("building host lists")
    azinstall.generate_hostlists(config, tmpdir)
    log.info("building install scripts")
    azinstall.generate_install(config, tmpdir, adminuser, private_key_file, public_key_file)
    
    jumpbox = config.get("install_from", None)
    fqdn = None
    if jumpbox:
        fqdn = azutil.get_fqdn(config["resource_group"], jumpbox+"pip")
        log.info("running install scripts")
        azinstall.run(config, tmpdir, adminuser, private_key_file, public_key_file, fqdn)
    else:
        log.info("nothing to install ('install_from' is not set)")

def do_destroy(args):
    log.info("reading config file ({})".format(args.config_file))
    config = azconfig.ConfigFile()  
    config.open(args.config_file)

    log.warning("deleting entire resource group ({})".format(config.read_value("resource_group")))
    if not args.force:
        log.info("you have 10s to change your mind and ctrl-c!")
        time.sleep(10)
        log.info("too late!")

    azutil.delete_resource_group(
        config.read_value("resource_group"), args.no_wait
    )

if __name__ == "__main__":
    azhpc_parser = argparse.ArgumentParser(prog="azhpc")
    
    gopt_parser = argparse.ArgumentParser()
    gopt_parser.add_argument(
        "--config-file", "-c", type=str, 
        default="config.json", help="config file"
    )
    gopt_parser.add_argument(
        "--debug", 
        help="increase output verbosity",
        action="store_true"
    )

    subparsers = azhpc_parser.add_subparsers(help="actions")

    build_parser = subparsers.add_parser(
        "build", 
        parents=[gopt_parser],
        add_help=False,
        description="deploy the config",
        help="create an arm template and deploy"
    )
    build_parser.set_defaults(func=do_build)
    build_parser.add_argument(
        "--output-template", 
        "-o", 
        type=str, 
        default="deploy.json", 
        help="filename for the arm template",
    )

    connect_parser = subparsers.add_parser(
        "connect", 
        parents=[gopt_parser],
        add_help=False,
        description="connect to a resource",
        help="connect to a resource with 'ssh'"
    )
    connect_parser.set_defaults(func=do_connect)
    connect_parser.add_argument(
        "--user", 
        "-u", 
        type=str,
        help="the user to connect as",
    )
    connect_parser.add_argument(
        "resource", 
        type=str,
        help="the resource to connect to"
    )
    connect_parser.add_argument(
        'args', 
        nargs=argparse.REMAINDER,
        help="additional arguments will be passed to the ssh command"
    )

    destroy_parser = subparsers.add_parser(
        "destroy", 
        parents=[gopt_parser],
        add_help=False,
        description="delete the resource group",
        help="delete entire resource group"
    )
    destroy_parser.set_defaults(func=do_destroy)
    destroy_parser.add_argument(
        "--force", 
        action="store_true",
        default=False,
        help="delete resource group immediately"
    )
    destroy_parser.add_argument(
        "--no-wait", 
        action="store_true",
        default=False,
        help="do not wait for resources to be deleted"
    )
    
    get_parser = subparsers.add_parser(
        "get",
        parents=[gopt_parser],
        add_help=False,
        description="get a config value",
        help="evaluate the value at the json path specified"
    )
    get_parser.set_defaults(func=do_get)
    get_parser.add_argument(
        "path", 
        type=str,
        help="the json path to evaluate"
    )

    init_parser = subparsers.add_parser(
        "init",
        parents=[gopt_parser],
        add_help=False,
        description="initialise a project",
        help="copy a file or directory with config files"
    )
    init_parser.set_defaults(func=do_init)
    init_parser.add_argument(
        "--show", 
        "-s", 
        action="store_true",
        default=False,
        help="display all vars that are <NOT-SET>"
    )
    init_parser.add_argument(
        "--dir", 
        "-d", 
        type=str,
        help="output directory",
    )
    init_parser.add_argument(
        "--vars", 
        "-v", 
        type=str,
        help="variables to replace in format VAR=VAL(,VAR=VAL)*",
    )

    preprocess_parser = subparsers.add_parser(
        "preprocess", 
        parents=[gopt_parser],
        add_help=False,
        description="preprocess the config file",
        help="expand all the config macros"
    )
    preprocess_parser.set_defaults(func=do_preprocess)

    run_parser = subparsers.add_parser(
        "run", 
        parents=[gopt_parser],
        add_help=False,
        description="run a command on the specified resources",
        help="run command on resources"
    )
    run_parser.set_defaults(func=do_run)
    run_parser.add_argument(
        "--user", 
        "-u", 
        type=str,
        help="the user to run as"
    )
    run_parser.add_argument(
        "--nodes", 
        "-n", 
        type=str,
        help="the resources to run on (space separated for multiple)"
    )
    run_parser.add_argument(
        'args', 
        nargs=argparse.REMAINDER,
        help="the command to run"
    )

    scp_parser = subparsers.add_parser(
        "scp", 
        parents=[gopt_parser],
        add_help=False,
        description="secure copy",
        help="copy files to a resource with 'scp'"
    )
    scp_parser.set_defaults(func=do_scp)
    scp_parser.add_argument(
        'args', 
        nargs=argparse.REMAINDER,
        help="the arguments passed to scp (use '--' to separate scp arguments)"
    )

    status_parser = subparsers.add_parser(
        "status", 
        parents=[gopt_parser],
        add_help=False,
        description="show status of all the resources",
        help="displays the resource uptime"
    )
    status_parser.set_defaults(func=do_status)

    args = azhpc_parser.parse_args()
    log.debug(args)
    
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s:%(filename)s:%(lineno)d:%(levelname)s:%(message)s')
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(message)s')

    args.func(args)

