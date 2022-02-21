'''\
Entry file for the benchmark.
Parse the config file, and continue from there on.

Check the documentation and help for more information.
'''

import argparse
import os.path
import sys
import logging
import os
import time
import configparser

import infrastructure.start as infrastructure
import resource_manager.start as resource_manager
import benchmark.start as benchmark
import results.start as results


def make_wide(formatter, w=120, h=36):
    """Return a wider HelpFormatter

    Args:
        formatter (HelpFormatter): Format class for Python Argparse
        w (int, optional): Width of Argparse output. Defaults to 120.
        h (int, optional): Max help positions for Argparse output. Defaults to 36.

    Returns:
        formatter: Format class for Python Argparse, possibly with updated output sizes
    """
    try:
        kwargs = {'width': w, 'max_help_position': h}
        formatter(None, **kwargs)
        return lambda prog: formatter(prog, **kwargs)
    except TypeError:
        print('Argparse help formatter failed, falling back.')
        return formatter


def option_check(parser, config, new, section, option, intype, condition, mandatory=True):
    """Check if each config option is present, if the type is correct, and if the value is correct.

    Args:
        config (ConfigParser): ConfigParser object
        new (dict): Parsed configuration
        section (str): Section in the config file
        option (str): Option in a section of the config file
        intype (type): Option should be type
        condition (lambda): Option should have these values
        mandatory (bool, optional): Is option mandatory. Defaults to True.
    """
    if config.has_option(section, option):
        # If option is empty, but not mandatory, remove option
        if config[section][option] == '':
            if mandatory:
                parser.error('Config: Missing option %s->%s' % (section, option))
            return

        # Check type
        try:
            if intype == int:
                val = config[section].getint(option)
            elif intype == float:
                val = config[section].getfloat(option)
            elif intype == bool:
                val = config[section].getboolean(option)
            elif intype == str:
                val = config[section][option]
            elif intype == list:
                val = config[section][option].split(',')
                val = [s for s in val if s.strip()]
                if val == []:
                    return
            else:
                parser.error('Config: Invalid type %s' % (intype))
        except ValueError:
            parser.error('Config: Invalid type for option %s->%s, expected %s' % (section, option, intype))

        # Check value
        if not condition(val):
            parser.error('Config: Invalid value for option %s->%s' % (section, option))

        new[section][option] = val
    elif mandatory:
        parser.error('Config: Missing option %s->%s' % (section, option))


def parse_config(parser, arg):
    """Parse config file, check valid input

    Args:
        parser (ArgumentParser): Argparse object
        arg (str): Path to a config file

    Returns:
        configParser: Parsed config file
    """
    if not (os.path.exists(arg) and os.path.isfile(arg)):
        parser.error("The given config file does not exist: %s" % (arg))

    config = configparser.ConfigParser()
    config.read(arg)

    # Parsed values will be saved in a dict because ConfigParser can only hold strings
    new = dict()

    sec = 'infrastructure'
    if config.has_section(sec):
        new[sec] = dict()
        option_check(parser, config, new, sec, 'provider', str, lambda x : x in ['qemu'])
        option_check(parser, config, new, sec, 'infra_only', bool, lambda x : x in [True, False])
        option_check(parser, config, new, sec, 'cloud_nodes', int, lambda x : x >= 0)
        option_check(parser, config, new, sec, 'edge_nodes', int, lambda x : x >= 0)
        option_check(parser, config, new, sec, 'endpoint_nodes', int, lambda x : x >= 0)
        option_check(parser, config, new, sec, 'cloud_cores', int, lambda x : x >= 2)
        option_check(parser, config, new, sec, 'edge_cores', int, lambda x : x >= 1)
        option_check(parser, config, new, sec, 'endpoint_cores', int, lambda x : x >= 1)
        option_check(parser, config, new, sec, 'cloud_quota', float, lambda x : 0.1 <=x <= 1.0)
        option_check(parser, config, new, sec, 'edge_quota', float, lambda x : 0.1 <=x <= 1.0)
        option_check(parser, config, new, sec, 'endpoint_quota', float, lambda x : 0.1 <=x <= 1.0)
        option_check(parser, config, new, sec, 'network_preset', str, lambda x : x in ['4g', '5g'], mandatory=False)

        # # Only check these options if network_preset is not used
        if not config.has_option(sec, 'network_preset'):
            option_check(parser, config, new, sec, 'cloud_latency_avg', float, lambda x : x >= 5.0)
            option_check(parser, config, new, sec, 'cloud_latency_var', float, lambda x : x >= 0.0)
            option_check(parser, config, new, sec, 'cloud_throughput', float, lambda x : x >= 1.0)
            option_check(parser, config, new, sec, 'endpoint_latency_avg', float, lambda x : x >= 5.0)
            option_check(parser, config, new, sec, 'endpoint_latency_var', float, lambda x : x >= 0.0)
            option_check(parser, config, new, sec, 'endpoint_throughput', float, lambda x : x >= 1.0)
    
        option_check(parser, config, new, sec, 'external_physical_machines', list, lambda x : True, mandatory=False)
    else:
        parser.error('Config: infrastructure section missing')

    # Total number of nodes > 0
    cloud = new[sec]['cloud_nodes']
    edge = new[sec]['edge_nodes']
    endpoint = new[sec]['endpoint_nodes']
    if cloud + edge + endpoint == 0:
        parser.error('Config: number of cloud+edge+endpoint nodes should be >= 1, not 0')

    # Check deployment mode in case we use the resource_manager and/or benchmark
    mode = 'endpoint'
    if edge:
        mode = 'edge'
    elif cloud:
        mode = 'cloud'

    # Check resource manager
    sec = 'resource_manager'
    if not new['infrastructure']['infra_only'] and config.has_section(sec):
        new[sec] = dict()

        # Can only use RM when you create the correct nodes
        if config.has_option(sec, 'cloud_rm') and new['infrastructure']['cloud_nodes'] > 0:
            option_check(parser, config, new, sec, 'cloud_rm', str, lambda x : x in ['kubernetes'])
        elif config.has_option(sec, 'cloud_rm') and new['infrastructure']['cloud_nodes'] == 0:
            parser.error('Config: cloud_rm has been set, but cloud_nodes=0')
        elif not config.has_option(sec, 'cloud_rm') and new['infrastructure']['cloud_nodes'] > 0:
            parser.error('Config: cloud_nodes>0, but no cloud_rm has been set while infra_only=False')

        # Same checks for edge RM
        if config.has_option(sec, 'edge_rm') and new['infrastructure']['edge_nodes'] > 0:
            option_check(parser, config, new, sec, 'edge_rm', str, lambda x : x in ['kubeedge'])
        elif config.has_option(sec, 'edge_rm') and new['infrastructure']['edge_nodes'] == 0:
            parser.error('Config: edge_rm has been set, but edge_nodes=0')
        elif not config.has_option(sec, 'edge_rm') and new['infrastructure']['edge_nodes'] > 0:
            parser.error('Config: edge_nodes>0, but no edge_rm has been set while infra_only=False')

        # Endpoint-only mode, this section is not needed
        if new[sec] == dict():
            parser.error('Config: resource_manager section declared but empty')
    elif new['infrastructure']['infra_only'] and config.has_section(sec):
        parser.error('Config: resource_manager section is present but infra_only=True')
    elif not new['infrastructure']['infra_only'] and not config.has_section(sec) and mode != 'endpoint':
        parser.error('Config: no resource_manager section is present but infra_only=False and not endpoint-only')

    # Check benchmark
    sec = 'benchmark'
    if not new['infrastructure']['infra_only'] and config.has_section(sec):
        new[sec] = dict()
        option_check(parser, config, new, sec, 'mode', str, lambda x : x in ['cloud', 'edge', 'endpoint'])
        option_check(parser, config, new, sec, 'docker_pull', bool, lambda x : x in [True, False])
        option_check(parser, config, new, sec, 'delete', bool, lambda x : x in [True, False])
        option_check(parser, config, new, sec, 'netperf', bool, lambda x : x in [True, False])
        option_check(parser, config, new, sec, 'application', str, lambda x : x in ['image_classification'])
        option_check(parser, config, new, sec, 'frequency', int, lambda x : x >= 1)

        # Extended checks: Number of nodes should match deployment mode
        if mode == 'cloud' and ((cloud < 2 or edge != 0 or endpoint == 0) or cloud % endpoint != 0):
            parser.error('Config: For cloud benchmark, #clouds>1, #edges=0, #endpoints>0, and (#clouds-1) % #endpoints=0')
        elif mode == 'edge' and ((cloud != 1 or edge == 0 or endpoint == 0) or edge % endpoint != 0):
            parser.error('Config: For edge benchmark, #clouds=1, #edges>0, #endpoints>0, and #edges % #endpoints=0')
        elif mode == 'endpoint' and (cloud != 0 or edge != 0 or endpoint == 0):
            parser.error('Config: For endpoint benchmark, #clouds=0, #edges=0, and #endpoints>0')
        elif mode != new["benchmark"]['mode']:
            parser.error('Mismatch between predicted mode and actual mode')
    elif new['infrastructure']['infra_only'] and config.has_section(sec):
        parser.error('Config: benchmark section is present but infra_only=True')

    return new


def add_constants(config):
    """Add some constants to the config dict

    Args:
        config (dict): Parsed configuration
    """    
    config['home'] = str(os.getenv('HOME'))
    config['base'] = str(os.path.dirname(os.path.realpath(__file__)))

    if not config['infrastructure']['infra_only']:
        if config['benchmark']['application'] == 'image_classification':
            config['images'] = ['redplanet00/kubeedge-applications:image_classification_subscriber',
                                'redplanet00/kubeedge-applications:image_classification_publisher',
                                'redplanet00/kubeedge-applications:image_classification_combined']

    config['prefixIP'] = '192.168.122'
    config['postfixIP'] = 10


def set_logging(args):
    """Enable logging to both stdout and file (BENCHMARK_FOLDER/logs)
    If the -v / --verbose option is used, stdout will report logging.DEBUG, otherwise only logging.INFO
    The file will always use logging.DEBUG (which is the bigger scope)

    Args:
        args (Namespace): Argparse object
    """
    # Log to file parameters
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    t = time.strftime("%Y-%m-%d_%H:%M:%S", time.gmtime())

    if args.config['infrastructure']['infra_only']:
        log_name = '%s_infra_only.log' % (t)
    else:
        log_name = '%s_%s_%s.log' % (t, args.config['benchmark']['mode'], args.config['benchmark']['application'])

    file_handler = logging.FileHandler(log_dir + '/' + log_name)
    file_handler.setLevel(logging.DEBUG)

    # Log to stdout parameters
    stdout_handler = logging.StreamHandler(sys.stdout)
    if args.verbose:
        stdout_handler.setLevel(logging.DEBUG)
    else:
        stdout_handler.setLevel(logging.INFO)

    # Set parameters
    logging.basicConfig(format="[%(asctime)s %(filename)20s:%(lineno)4s - %(funcName)25s() ] %(message)s", 
        level=logging.DEBUG, datefmt='%Y-%m-%d %H:%M:%S', handlers=[file_handler, stdout_handler])

    logging.info('Logging has been enabled. Writing to stdout and file at %s/%s' % (log_dir, log_name))

    s = []
    header = True
    for key, value in args.config.items():
        if type(value) is dict:
            s.append('[' + key + ']')
            for k, v in dict(args.config[key]).items():
                s.append('%-30s = %s' % (k, v))

            s.append('')
        else:
            if header:
                s.append('[constants]')
                header = False

            if type(value) is list:
                s.append('%-30s = %s' % (key, value[0]))
                if len(value) > 1:
                    for v in value[1:]:
                        s.append('%-30s   %s' % ('', v))
            else:
                s.append('%-30s = %s' % (key, value))

    logging.debug('\n' + '\n'.join(s))


def main(args):
    """Main control function of the framework

    Args:
        args (Namespace): Argparse object
    """    
    machines = infrastructure.start(args.config)

    if not args.config['infrastructure']['infra_only']:
        resource_manager.start(args.config, machines)
        output = benchmark.start(args.config, machines)
        results.start(args.config, machines, output)

        if args.config['benchmark']['delete']:
            infrastructure.delete(machines)


if __name__ == '__main__':
    """Get input arguments, and validate those arguments
    """
    parser = argparse.ArgumentParser(
        formatter_class=make_wide(argparse.HelpFormatter, w=120, h=500))

    parser.add_argument('config', type=lambda x: parse_config(parser, x),
        help='benchmark config file')
    parser.add_argument('-v', '--verbose', action='store_true',
        help='increase verbosity level')

    args = parser.parse_args()

    # Set loggers
    add_constants(args.config)
    set_logging(args)

    main(args)
