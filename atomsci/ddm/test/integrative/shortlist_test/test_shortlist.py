#!/usr/bin/env python

import shutil
import json
import subprocess
import os
import time
import pandas as pd

import tempfile
import tarfile
import json

import atomsci.ddm.pipeline.parameter_parser as parse
import atomsci.ddm.pipeline.compare_models as cm
from atomsci.ddm.utils import llnl_utils
import atomsci.ddm.utils.file_utils as futils

def init_data():
    '''
    Copy files necessary for running tests
    '''
    if not os.path.exists('data'):
        os.makedirs('data')

    shutil.copyfile('../../test_datasets/MRP3_dataset.csv', 'data/MRP3_dataset.csv')
    shutil.copyfile('../../test_datasets/delaney-processed_curated_fit.csv', 'data/delaney-processed_curated_fit.csv')

def clean():
    """
    Clean test files

    Args:
        split_uuids list(str): Remove split files in this list

    """
    if "shortlist_test" in os.listdir():
        shutil.rmtree("shortlist_test")

    if "logs" in os.listdir():
        shutil.rmtree("logs")

    if "run.sh" in os.listdir():
        os.remove("run.sh")

    if "slurm_files" in os.listdir():
        shutil.rmtree("slurm_files")
    
    if "test_shortlist_with_uuids.csv" in os.listdir():
        os.remove("test_shortlist_with_uuids.csv")

    if "data" in os.listdir():
        shutil.rmtree("data")

def wait_to_finish(split_json, search_json, max_time=1200):
    """ Run hyperparam search and return pref_df

    Given parased parameter namespace build the hyperparam search command and
    wait for training to complete. Once training is complete, retrun the perf_df.
    This function repeatedly calls get_filesystem_perf_results until it sees
    at least the number of jobs generated by pparams.

    Args:
        split_json (str): Path to split_json file to run.
        
        search_json (str): Path to search_json file to run.

        max_type (int): Max wait time in seconds. Default 600. -1 is unlimited
            wait time.

    Returns:
        DataFrame or None: returns perf_df if training completes in time. 

    """
    with open(split_json, "r") as f:
        hp_params = json.load(f)

    pparams = parse.wrapper(hp_params)
    
    script_dir = pparams.script_dir
    python_path = pparams.python_path
    result_dir = pparams.result_dir
    pred_type = pparams.prediction_type
    
    slkey = pparams.shortlist_key
    slkey = slkey.replace('.csv','')
    slkey = os.path.join('test/integrative/shortlist_test/',slkey)
    shortlist_path = os.path.join(script_dir, f'{slkey}.csv')
    print(shortlist_path)
    features = pparams.descriptor_type
    shortlist_df = pd.read_csv(shortlist_path)
    dataset_key = shortlist_df['dataset_key'].iloc[-1].replace('.csv', f'_with_{features}_descriptors.csv')
    dset_path, dataset_key = dataset_key.rsplit(sep='/', maxsplit=1)
    feat_path = dset_path+'/scaled_descriptors/'+dataset_key
    
    # Featurize shortlist
    print("Submitting batch featurization job")
    run_cmd = f"{python_path} {os.path.join(script_dir, 'test/integrative/shortlist_test/featurize_shortlist.py')} {shortlist_path} {split_json}"
    p = subprocess.Popen(run_cmd.split(' '), stdout=subprocess.PIPE)
    out = p.stdout.read().decode("utf-8")
    num_jobs = 1
    num_found = 0
    time_waited = 0
    wait_interval = 30
    print("Waiting for shortlist featurization to finish. Checks every 30 seconds")
    while (num_found < num_jobs) and ((max_time == -1) or (time_waited < max_time)):
        # wait until the training jobs have finished
        try:
            feat_df = pd.read_csv(feat_path)
            num_found = feat_df.shape[0]
        except:
            num_found = 0
            feat_df = None
            time.sleep(wait_interval) # check for results every 30 seconds
            time_waited += wait_interval
        print(f'waited {time_waited} found {num_found}')
    
    shortlist_path = os.path.join(script_dir, f'{slkey}_with_uuids.csv')
    
    # Split shortlist
    print("Submitting shortlist split job")
    run_cmd = f"{python_path} {script_dir}/utils/hyperparam_search_wrapper.py --config_file {split_json}"
    p = subprocess.Popen(run_cmd.split(' '), stdout=subprocess.PIPE)
    out = p.stdout.read().decode("utf-8")
    num_jobs=1
    num_found = 0
    time_waited = 0
    wait_interval = 30
    print("Waiting for shortlist splitting to finish. Checks every 30 seconds")
    while (num_found < num_jobs) and ((max_time == -1) or (time_waited < max_time)):
        # wait until the training jobs have finished
        try:
            print(shortlist_path)
            shortlist_df = pd.read_csv(shortlist_path)
            print(script_dir)
            shortlist_df.to_csv(shortlist_path, index=False)
            num_found = num_jobs+1
        except:
            num_found = 0
            shortlist_df = None
            print("Still waiting")
            time.sleep(wait_interval) # check for results every 30 seconds
            time_waited += wait_interval

    # Test HP search with shortlist
    
    run_cmd = f"{python_path} {script_dir}/utils/hyperparam_search_wrapper.py --config_file {search_json}"
    print(f"hyperparam command: {run_cmd}")
    p = subprocess.Popen(run_cmd.split(' '), stdout=subprocess.PIPE)
    out = p.stdout.read().decode("utf-8")

    num_jobs = out.count('Submitted batch job')
    num_found = 0
    time_waited = 0
    wait_interval = 30

    print("Waiting on %d jobs to finish. Checks every 30 seconds" % num_jobs)
    result_df = None
    while (num_found < num_jobs) and ((max_time == -1) or (time_waited < max_time)):
        # wait until the training jobs have finished
        time.sleep(wait_interval) # check for results every 30 seconds
        time_waited += wait_interval
        try:
            result_df = cm.get_filesystem_perf_results(result_dir, pred_type=pred_type)
            num_found = result_df.shape[0]
        except:
            num_found = 0
            result_df = None
        print(f'waited {time_waited} found {num_found}')

    return result_df

def test():
    """
    Test full model pipeline: Split data, featurize data, fit model, get results
    """

    # Clean
    # -----
    clean()

    # Init Data
    # -----
    init_data()

    # Run shortlist hyperparam search
    # ------------
    if llnl_utils.is_lc_system():
        result_df = wait_to_finish("test_shortlist_split_config.json",
            "test_shortlist_RF-NN-XG_hyperconfig.json", max_time=-1)
        assert len(result_df) == 18 # Timed out
    else:
        assert True

    # Clean
    # -----
    clean()

def extract_split_uuid(tar_file):
    """
    Given a tar file, return split uuid used to train the model.
    """

    tmpdir = tempfile.mkdtemp()

    with tarfile.open(tar_file, mode='r:gz') as tar:
        futils.safe_extract(tar, path=tmpdir)

    # make metadata path
    metadata_path = os.path.join(tmpdir, 'model_metadata.json')

    with open(metadata_path, 'r') as json_file:
        json_dat = json.load(json_file)

    split_uuid = json_dat['splitting_parameters']['split_uuid']

    return split_uuid

if __name__ == '__main__':
    test()
