from typing import Iterable, Type, MutableMapping, Mapping, Any, Optional, Tuple, List
import  numpy as np
import logging
import csv
from collections import OrderedDict
import sys
import  os
import pathlib
import time

import  torch
import torch.backends.cudnn as cudnn
from torch import nn
from torch.optim import lr_scheduler, SGD, Adam
from warmup_scheduler import GradualWarmupScheduler
from torch.optim.lr_scheduler import _LRScheduler
from torch.optim.optimizer import Optimizer
from torch.nn.modules.loss import _WeightedLoss, _Loss
import torch.nn.functional as F
import torch.backends.cudnn as cudnn
from torchvision.datasets.utils import check_integrity, download_url

import yaml
import subprocess

class AverageMeter:

    def __init__(self):
        self.reset()

    def reset(self):
        self.avg = 0.
        self.sum = 0.
        self.cnt = 0
        self.last = 0.

    def update(self, val, n=1):
        self.last = val
        self.sum += val * n
        self.cnt += n
        self.avg = self.sum / self.cnt

def first_or_default(it:Iterable, default=None):
    for i in it:
        return i
    return default

def deep_update(d:MutableMapping, u:Mapping, map_type:Type[MutableMapping]=dict)\
        ->MutableMapping:
    for k, v in u.items():
        if isinstance(v, Mapping):
            d[k] = deep_update(d.get(k, map_type()), v, map_type)
        else:
            d[k] = v
    return d

def state_dict(val)->Mapping:
    assert hasattr(val, '__dict__'), 'val must be object with __dict__'

    # Can't do below because val has state_dict() which calls utils.state_dict
    # if has_method(val, 'state_dict'):
    #     d = val.state_dict()
    #     assert isinstance(d, Mapping)
    #     return d

    return {'yaml': yaml.dump(val)}

def load_state_dict(val:Any, state_dict:Mapping)->None:
    assert hasattr(val, '__dict__'), 'val must be object with __dict__'

    # Can't do below because val has state_dict() which calls utils.state_dict
    # if has_method(val, 'load_state_dict'):
    #     return val.load_state_dict(state_dict)

    s = state_dict.get('yaml', None)
    assert s is not None, 'state_dict must contain yaml key'

    obj = yaml.load(s, Loader=yaml.Loader)
    for k in val.__dict__.keys():
        setattr(val, k, getattr(obj, k))

def deep_comp(o1:Any, o2:Any)->bool:
    # NOTE: dict don't have __dict__
    o1d = getattr(o1, '__dict__', None)
    o2d = getattr(o2, '__dict__', None)

    # if both are objects
    if o1d is not None and o2d is not None:
        # we will compare their dictionaries
        o1, o2 = o1.__dict__, o2.__dict__

    if o1 is not None and o2 is not None:
        # if both are dictionaries, we will compare each key
        if isinstance(o1, dict) and isinstance(o2, dict):
            for k in set().union(o1.keys() ,o2.keys()):
                if k in o1 and k in o2:
                    if not deep_comp(o1[k], o2[k]):
                        return False
                else:
                    return False # some key missing
            return True
    # mismatched object types or both are scalers, or one or both None
    return o1 == o2

def is_debugging()->bool:
    return 'pydevd' in sys.modules # works for vscode

def full_path(path:str)->str:
    path = os.path.expandvars(path)
    path = os.path.expanduser(path)
    return os.path.abspath(path)

def zero_file(filepath)->None:
    """Creates or truncates existing file"""
    open(filepath, 'w').close()

def setup_logging(filepath:Optional[str]=None,
                  name:Optional[str]=None, level=logging.INFO)->logging.Logger:
    logger = logging.getLogger()

    # is it already setup?
    if len(logger.handlers)==2 and \
        isinstance(logger.handlers[0], logging.StreamHandler) and \
        isinstance(logger.handlers[1], logging.FileHandler):
            return logger

    assert len(logger.handlers)==0, 'Root logger has unexpected setup!'

    logger.setLevel(level)
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M'))
    logger.addHandler(ch)
    logger.propagate = False # otherwise root logger prints things again

    if filepath:
        filepath = full_path(filepath)
        # log files gets appeneded if already exist
        # zero_file(filepath)
        fh = logging.FileHandler(filename=full_path(filepath))
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter('[%(asctime)s][%(levelname)s] %(message)s'))
        logger.addHandler(fh)
    return logger

def fmt(val:Any)->str:
    if isinstance(val, float):
        return f'{val:.4g}'
    return str(val)

def append_csv_file(filepath:str, new_row:List[Tuple[str, Any]], delimiter='\t'):
    fieldnames, rows = [], []
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            dr = csv.DictReader(f, delimiter=delimiter)
            fieldnames = dr.fieldnames
            rows = [row for row in dr.reader]
    if fieldnames is None:
        fieldnames = []

    new_fieldnames = OrderedDict([(fn, None) for fn, v in new_row])
    for fn in fieldnames:
        new_fieldnames[fn]=None

    with open(filepath, 'w', newline='') as f:
        dr = csv.DictWriter(f, fieldnames=new_fieldnames.keys(), delimiter=delimiter)
        dr.writeheader()
        for row in rows:
            d = dict((k,v) for k,v in zip(fieldnames, row))
            dr.writerow(d)
        dr.writerow(OrderedDict(new_row))

def has_method(o, name):
    return callable(getattr(o, name, None))

def extract_tar(src, dest=None, gzip=None, delete=False):
    import tarfile

    if dest is None:
        dest = os.path.dirname(src)
    if gzip is None:
        gzip = src.lower().endswith('.gz')

    mode = 'r:gz' if gzip else 'r'
    with tarfile.open(src, mode) as tarfh:
        tarfh.extractall(path=dest)

    if delete:
        os.remove(src)

def download_and_extract_tar(url, download_root, extract_root=None, filename=None,
                             md5=None, **kwargs):
    download_root = os.path.expanduser(download_root)
    if extract_root is None:
        extract_root = download_root
    if filename is None:
        filename = os.path.basename(url)

    if not check_integrity(os.path.join(download_root, filename), md5):
        download_url(url, download_root, filename=filename, md5=md5)

    extract_tar(os.path.join(download_root, filename), extract_root, **kwargs)

def setup_cuda(seed):
    seed = int(seed)
    # setup cuda
    cudnn.enabled = True
    np.random.seed(seed)
    torch.manual_seed(seed)
    #torch.cuda.manual_seed_all(seed)
    cudnn.benchmark = True
    #cudnn.deterministic = False
    # torch.cuda.empty_cache()
    # torch.cuda.synchronize()

def cuda_device_names()->str:
    return ', '.join([torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())])

def exec_shell_command(command:str, print_command=True)->None:
    if print_command:
        print(command)
    subprocess.run(command, shell=True, check=True)