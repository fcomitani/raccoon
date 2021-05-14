
"""
RACCOON (Recursive Algorithm for Coarse-to-fine Clustering OptimizatiON)
F. Comitani     @2018-2021
A. Maheshwari   @2019
"""

import os

import logging
DEBUG_R = 15

import time
import psutil
import warnings

import csv

import raccoon.utils.functions as functions
import raccoon.interface as interface

from raccoon.clustering import *
from raccoon.classification import KNN
from raccoon.update import UpdateClusters

def cluster(data, **kwargs):
    """ Wrapper function to setup, create a RecursiveClustering object,
        run the recursion and logging.

        Args:
            data (pandas dataframe): dataframe with sampels as rows and features as columns.
            kwargs (dict): keyword arguments for RecursiveClustering. 

        Returns:
            clus_opt (pandas dataframe): one-hot-encoded clusters membership of data.
            tree (anytree object): anytree structure with information on the clusters
                                   hierarchy.
    """

    start_time = time.time()

    if 'outpath' not in kwargs or kwargs['outpath'] is None:
        kwargs['outpath'] = os.getcwd()
    if 'RPD' not in kwargs:
        kwargs['RPD'] = False
    if 'chk' not in kwargs:
        kwargs['chk'] = False

    """ Setup folders and files, remove old data if present. """

    functions.setup(kwargs['outpath'], True, kwargs['chk'], kwargs['RPD'])

    logging.info('Starting a new clustering run')
    
    """ Run recursive clustering algorithm. """

    obj = RecursiveClustering(data, **kwargs)
    obj.recurse()

    """ Save the assignment to disk and buil tree. """

    tree = None
    if obj.clus_opt is not None:
        obj.clus_opt.to_hdf(
            os.path.join(
                kwargs['outpath'], 'raccoon_data/clusters_final.h5'),
            key='df')
        tree = trees.build_tree(
            obj.clus_opt, outpath=os.path.join(
                kwargs['outpath'], 'raccoon_data/tree_final.json'))

    """ Log the total runtime and memory usage. """

    logging.info('=========== Final Clustering Results ===========')
    if obj.clus_opt is not None:
        logging.info('A total of {:d} clusters were found'.format(
            len(obj.clus_opt.columns)))
    else:
        logging.info('No clusters found! Try changing the search parameters')
    logging.info(
        'Total time of the operation: {:.3f} seconds'.format(
            (time.time() - start_time)))
    logging.info(psutil.virtual_memory())

    return obj.clus_opt, tree


def resume(data, refpath, lab=None, **kwargs):

    """ Wrapper function to resume a RecursiveClustering run 
        from checkpoint files.

        Args:
            data (pandas dataframe): dataframe with sampels as rows and features as columns.
            refpath (string): path to checkpoint files parent folder.
            lab (list, array or pandas series): list of labels corresponding to each sample
                                                (for plotting only).
            kwargs (dict): keyword arguments for KNN and RecursiveClustering. 

        Returns:
            new_clus (pandas dataframe): one-hot-encoded clusters membership of the 
                                        whole data.
            tree (anytree object): anytree structure with information on the clusters
                                   hierarchy.
    """

    start_time = time.time()
    

    if 'outpath' not in kwargs or kwargs['outpath'] is None:
        kwargs['outpath'] = os.getcwd()
    if 'RPD' not in kwargs:
        kwargs['RPD'] = False
    if 'popcut' not in kwargs:
        kwargs['popcut'] = 50 #default value
    if 'maxdepth' not in kwargs:
        kwargs['maxdepth'] = None  #default value
    if 'depth' in kwargs:
        del kwargs['depth']
    if 'chk' not in kwargs:
        kwargs['chk'] = True

    """ Setup CPU/GPU interface. """

    if kwargs['gpu']:
        try:
            intf = interface.InterfaceGPU()
        except BaseException:
            warnings.warn("No RAPIDS found, running on CPU instead.")
            kwargs['gpu'] = False

    if not kwargs['gpu']:
        intf = interface.InterfaceCPU()

    """ Load parameters file. """

    try:
        oldparams = intf.df.read_csv(os.path.join(refpath,'raccoon_data/paramdata.csv'))
    except:
        sys.exit('ERROR: there was a problem loading the paramdata.csv, make sure the file path is correct.')    
        
    if oldparams.shape[0] == 0:
        sys.exit('ERROR: paramdata.csv is empty.')
   
    """ Load clustering files and join them. """    

    old_clus=[]
    for filename in os.listdir(os.path.join(refpath,'raccoon_data/chk')):
         old_clus.append(intf.df.read_hdf(os.path.join(refpath,'raccoon_data/chk',filename))\
            .reindex(data.index))
    
    if len(old_clus) == 0:
        sys.exit('ERROR: there was a problem loading the checkpoint files, make sure the file path is correct.\n'+\
                  'If no checkpoint files were generated repeat the run from scratch.')    

    elif len(old_clus) > 1: 
        # for now join not available in cudf concat 
        old_clus = intf.df.concat(
            old_clus, axis=1)
        old_clus = old_clus.fillna(0)
    
    else:
        old_clus = old_clus[0]

    #old_clus=old_clus[functions.sort_len_num(intf.get_value(old_clus.columns))]
    old_clus=old_clus[functions.sort_len_num(old_clus.columns)]
    new_clus=[old_clus]

    """ Setup logging."""

    functions.setup(kwargs['outpath'], True, kwargs['chk'], kwargs['RPD'], delete=False)

    logging.info('Resuming clustering run.')

    """ Fix discrepancies between parameters file and clusters. """

    to_drop = [x for x in intf.get_value(oldparams.index) if oldparams['name'].loc[x] != '0' and
               oldparams['name'].loc[x] not in old_clus.columns]

    if len(to_drop) > 0:
        logging.warning('Discrepancies between parameter file and clusters'+\
            'found in {:d} case(s)\n'.format(len(to_drop))+\
            'these will be automatically fixed.')

        oldparams.drop(to_drop,inplace=True)

    """ Identify clusters to resume. 
        Check population size, and depth. 
    """
    to_run = [x for x in old_clus.columns
                if x not in intf.get_value(oldparams['name']) and
                old_clus[x].sum()>kwargs['popcut'] and
                (kwargs['maxdepth'] is None or x.count('_') < kwargs['maxdepth'])]

    if len(to_run) == 0:
        logging.warning('No resumable cluster found. Your run might have completed succesfully.')
    
    else:

        """ Run recursive clustering algorithm. """

        for x in to_run:
            logging.info('Resuming cluster: '+x)
            cludata = data[intf.get_value(old_clus[x]==1)]
            clulevel = x.count('_')
            
            clulab = None
            if lab is not None:
                clulab = lab.loc[cludata.index]

            obj = RecursiveClustering(cludata, lab=clulab, depth=clulevel, name=x, **kwargs)
            obj.recurse()
            
            if obj.clus_opt is not None:
                new_clus.append(obj.clus_opt.astype(int))
    
            del obj

    if len(new_clus)>1:
        new_clus = intf.df.concat(new_clus,axis=1).fillna(0)
        #new_clus = new_clus[functions.sort_len_num(intf.get_value(new_clus.columns))]
        new_clus = new_clus[functions.sort_len_num(new_clus.columns)]
    else:
        new_clus = new_clus[0]

    """ Save the assignment to disk and buil tree. """

    tree = None
    if new_clus is not None:
        new_clus.to_hdf(
            os.path.join(
                kwargs['outpath'], 'raccoon_data/clusters_resumed_final.h5'),
            key='df')
        tree = trees.build_tree(
            new_clus, outpath=os.path.join(
                kwargs['outpath'], 'raccoon_data/tree_resumed_final.json'))

    """ Log the total runtime and memory usage. """

    logging.info('=========== Final Clustering Results ===========')
    if new_clus is not None:
        logging.info('A total of {:d} clusters were found'.format(
            len(new_clus.columns)))
    else:
        logging.info('No clusters found! Try changing the search parameters')

    logging.info(
        'Total time of the operation: {:.3f} seconds'.format(
            (time.time() - start_time)))
    logging.info(psutil.virtual_memory())

    return new_clus, tree


def classify(new_data, old_data, membership, refpath='./raccoon_data', **kwargs):
    """ Wrapper function to classify new data with KNN on
        a previous RecursiveClustering output.

       Args:
            new_data (matrix or pandas dataframe): data to classify in 
                dataframe-compatible format.
            old_data (matrix or pandas dataframe): reference data on which the 
                hierarchy was built.
            membership (matrix or pandas dataframe): one-hot-encoded clusters assignment 
                table from the original run. 
            refpath (string): path to the location where trained umap files (pkl) are 
                stored (default subdirectory racoon_data of current folder).
            kwargs (dict): keyword arguments for KNN.

        Returns:
            (pandas dataframe): one-hot-encoded clusters membership of the 
                                         projected data.
    """

    start_time = time.time()

    if 'outpath' not in kwargs or kwargs['outpath'] is None:
        kwargs['outpath'] = os.getcwd()

    """ Setup logging."""

    functions.setup(kwargs['outpath'], False, False, False, suffix='_knn',
        delete=False)

    """ Run classifier. """

    obj = KNN(new_data, old_data, membership, refpath=refpath, **kwargs)
    obj.assign_membership()

    """ Save the assignment to disk and buil tree. """

    if obj.membership is not None:
        obj.membership.to_hdf(
            os.path.join(
                kwargs['outpath'], 'raccoon_data/classification_final.h5'),
            key='df')

    """ Log the total runtime and memory usage. """

    logging.info(
        'Total time of the operation: {:.3f} seconds'.format(
            (time.time() - start_time)))
    logging.info(psutil.virtual_memory())

    return obj.membership


def update(new_data, old_data, membership, tolerance=1e-1, probcut=.25, refpath='./raccoon_data', 
            outpath='./', **kwargs):
    """ Wrapper function to update
        a previous RecursiveClustering output with new data.
        Runs KNN furst on the new data points to identify the closest matching
        clusters. These points are then added to each cluster along the heirarchy
        and the objective function is recalculated. If this score is lowered
        beyond the given threshold, the cluster under scrutiny is scrapped, 
        together with its offspring, and re-built from scrach.

       Args:
            new_data (matrix or pandas dataframe): data to classify in
                dataframe-compatible format.
            old_data (matrix or pandas dataframe): reference data on which the
                hierarchy was built.
            membership (matrix or pandas dataframe): one-hot-encoded clusters assignment
                table from the original run.
            tolerance (float): objective score change threshold, beyond which
                clusters will have to be recalculated.
            probcut (float): prubability cutoff, when running the KNN, samples
                with less than this value of probability to any assigned class will be
                treated as noise and won't impact the clusters score review.
            refpath (string): path to the location where trained umap files (pkl) are
                stored (default subdirectory racoon_data of current folder).
            outpath (string): path to the location where output files will be saved
                (default current folder).
            kwargs (dict): keyword arguments for KNN and RecursiveClustering.

        Returns:
            (pandas dataframe): one-hot-encoded perturbed clusters membership.
    """

    start_time = time.time()

    if 'RPD' not in kwargs:
        kwargs['RPD'] = False
    if 'chk' not in kwargs:
        kwargs['chk'] = False

    """ Setup logging."""

    functions.setup(outpath, True, kwargs['chk'], kwargs['RPD'], suffix='_upt',
        delete=False)

    logging.info('Starting clusters perturbation run.')

    obj = UpdateClusters(new_data, old_data, membership, refpath=refpath, 
        outpath=outpath, tolerance=tolerance, probcut=probcut, **kwargs)
    obj.find_and_update()

    """ Save the assignment to disk and buil tree. """

    tree = None
    if obj.new_clus is not None:
        obj.new_clus.to_hdf(
            os.path.join(
                outpath, 'raccoon_data/clusters_updated_final.h5'),
            key='df')
        tree = trees.build_tree(
            obj.new_clus, outpath=os.path.join(
                outpath, 'raccoon_data/tree_updated_final.json'))

    """ Log the total runtime and memory usage. """

    logging.info(
        'Total time of the operation: {:.3f} seconds'.format(
            (time.time() - start_time)))
    logging.info(psutil.virtual_memory())

    return obj.new_clus

if __name__ == "__main__":

    pass
