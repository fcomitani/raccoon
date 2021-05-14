
"""
Basic k-nearest neighbours classifier for RACCOON
(Recursive Algorithm for Coarse-to-fine Clustering OptimizatiON)
F. Comitani     @2020
"""

import os
import sys
import pickle

import logging
DEBUG_R = 15

import time
import psutil

import random

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import raccoon.utils.functions as functions
import raccoon.interface as interface
import raccoon.utils.plots as plotting

from raccoon.clustering import *
from raccoon.classification import KNN



class UpdateClusters:

    """ Adds new data to the dataset and identifies clusters that need to be updated.
    Runs KNN furst on the new data points to identify the closest matching
    clusters. These points are then added to each cluster along the heirarchy
    and the objective function is recalculated. If this score is lowered
    beyond the given threshold, the cluster under scrutiny is scrapped,
    together with its offspring, and re-built from scrach. 
    """
    
    def __init__(self, data, ori_data, ori_clu,
            refpath="./raccoon_data/", outpath="./",
            tolerance=1e-1, probcut=.25, minclusize=10,
            score='silhouette', metric_clu='cosine', 
            root='0', debug=False, gpu=False, **kwargs):
        """ Initialize the the class.

        Args:
            data (matrix or pandas dataframe): input data in pandas dataframe-compatible format
                (samples as row, features as columns).
            ori_data (matrix or pandas dataframe): original data clustered with RACCOON in pandas
                dataframe-compatible format (samples as row, features as columns).
            ori_clu (matrix or pandas dataframe): original RACCOON output one-hot-encoded class
                membership in pandas dataframe-compatible format
                (samples as row, classes as columns).
            refpath (string): path to the location where trained umap files (pkl) are stored
                (default subdirectory racoon_data of current folder).
            outpath (string): path to the location where outputs will be saved
                (default save to the current folder).
            tolerance (float): objective score change threshold, beyond which
                clusters will have to be recalculated (default 1e-1).
            probcut (float): prubability cutoff, when running the KNN, samples
                with less than this value of probability to any assigned class will be
                treated as noise and won't impact the clusters score review (default 0.25).
            minclusize (int): minimum number of samples in a cluster (default is 10).
            score (string): objective function of the optimization (currently only 'dunn'
                and 'silhouette' are available, default 'silhouette').
            metric_clu (string): metric to be used in clusters identification and clustering score
                calculations (default euclidean)
                Warning: 'cosine' does not work with HDBSCAN, normalize to 'l2' and use 'euclidean'
                instead.
            root (string): name of the root node, parent of all the classes within the first
                clustering level (default '0').
            debug (boolean): specifies whether algorithm is run in debug mode (default is False).
            gpu (bool): activate GPU version (requires RAPIDS).
            kwargs (dict): keyword arguments for RecursiveClustering.
        """

        self.start_time = time.time()

        self.gpu = gpu

        """ Set up for CPU or GPU run. """

        if self.gpu:
            try:
                self.interface = interface.InterfaceGPU()
            except BaseException:
                warnings.warn("No RAPIDS found, running on CPU instead.")
                self.gpu = False

        if not self.gpu:
            self.interface = interface.InterfaceCPU()

        if not isinstance(data, self.interface.df.DataFrame):
            try:
                data = self.interface.df.DataFrame(data)
            except BaseException:
                print('Unexpected error: ', sys.exc_info()[0])
                print('Input data should be in a format that can be translated '+\
                       'to pandas dataframe!')
                raise

        if not isinstance(ori_data, self.interface.df.DataFrame):
            try:
                ori_data = self.interface.df.DataFrame(ori_data)
            except BaseException:
                print('Unexpected error: ', sys.exc_info()[0])
                print('Input data (original) should be in a format that can be '+\
                       'translated to pandas dataframe!')
                raise

        if not isinstance(ori_clu, self.interface.df.DataFrame):
            try:
                ori_clu = self.interface.df.DataFrame(ori_clu)
            except BaseException:
                print('Unexpected error: ', sys.exc_info()[0])
                print('Input data (clusters) should be in a format that can be '+\
                       'translated to pandas dataframe!')
                raise

        self.ori_data = ori_data.astype(self.interface.num.float)
        self.data = data[self.ori_data.columns].astype(self.interface.num.float)
        self.ori_clu = ori_clu
        self.refpath = refpath
        self.outpath = outpath
        self.tolerance = tolerance
        self.probcut = probcut
        self.minclusize = minclusize
        self.score = score
        self.metric_clu = metric_clu
        self.root = root
        self.debug = debug
        self.kwargs = kwargs

        """ Setup logging. """

        #logname = 'raccoon_updt_' + str(os.getpid()) + '.log'
        #print('Log information will be saved to ' + logname)

        #logging.basicConfig(
        #    level=logging.INFO,
        #    filename=os.path.join(outpath, logname),
        #    filemode="a+",
        #    format="%(asctime)-15s %(levelname)-8s %(message)s")
        #logging.getLogger('matplotlib.font_manager').disabled = True

        if self.debug:
            logging.addLevelName(DEBUG_R, 'DEBUG_R')
            logging.getLogger().setLevel(DEBUG_R)
            self._seed = 32
        else:
            logging.getLogger().setLevel(logging.INFO)
            self._seed = random.randint(0,999999)

        self._umap_rs = self._seed
        logging.info(
            "Random seed is: {:d}".format(int(self._seed)))

        """ Load parameters file. """

        paramdata = self.interface.df.read_csv(
            os.path.join(self.refpath, 'paramdata.csv'))
        paramdata['name'] = paramdata['name'].astype(str)
        self.paramdata = paramdata.set_index('name', drop=True)

        """ Run KNN. """

        self.clu = self.run_knn()

        """ Apply pobability cutoff and assign each sample to a unique 
            path along the hierarchy. 
        """

        self.clu[self.clu<self.probcut]=0
        self.clu = functions.unique_assignment(self.clu, self.root, self.interface)
        
        self.new_clus = None

    def run_knn(self):
        """ Run a single KNN instance to project the new 
            datapoint onto the old hierarchy. 
            
        Returns:
            (dataframe): the cluster assignment one-hot-encoded
                dataframe for the new dataset.
        """

        obj = KNN(self.data, self.ori_data, self.ori_clu, 
                    refpath=self.refpath, outpath=self.outpath,
                    root=self.root, debug=self.debug, gpu=self.gpu)
        obj.assign_membership()

        return obj.membership

    def single_update(self, clu_name):
        """ Update the clusters by adding the new data and rebuilding
            them if needed.
            
        Args:i
            clu_name (str): name of the cluster to update.

        Returns:
            (dataframe): the updated cluster assignment one-hot-encoded
                dataframe for the given cluster.
        """

        children = [x for x in self.ori_clu.columns if 
            x.startswith(clu_name) and x.count('_') == clu_name.count('_')+1]
        
        if clu_name != self.root:
            ori_samples = self.ori_clu[self.ori_clu[clu_name]==1]
            samples = self.clu[self.clu[clu_name]==1]
        else:
            ori_samples = self.ori_clu
            samples = self.clu
        
        """ Load projection. """
        
        proj = self.interface.df.read_hdf(os.path.join(self.outpath,
            'raccoon_data/' + clu_name + '_knn.h5'))
         
        if proj.shape[1] !=2:
            proj2d = self.interface.df.read_hdf(os.path.join(self.outpath,
                'raccoon_data/' + clu_name + '_2d_knn.h5'))
        else:
            proj2d = proj

        """ Reformat assignments vectors. """

        if len(children)==0:
        
            ori_assign_vec = pd.Series(0, index=ori_samples.index)
            assign_vec = self.interface.df.Series(0, index=samples.index)
        
        else:
            tmp_clust = ori_samples[children]
            noise = tmp_clust[tmp_clust.sum(axis=1) == 0].index
            #ori_assign_vec = tmp_clust.idxmax(axis=1)
            #ridicolous cudf workaround
            ori_assign_vec = self.interface.df.Series(
                tmp_clust.columns[
                self.interface.get_value(
                self.interface.num.argmax(
                self.interface.get_value(tmp_clust),axis=1))])
            #cudf workaround
            ori_assign_vec.index = tmp_clust.index
            
            ori_assign_vec.loc[noise] = -1
        
            #itmp_clust = self.interface.df.concat([tmp_clust,
            #    self.clu[children]], axis=0)
            
            tmp_clust = samples[children]
            noise = tmp_clust[tmp_clust.sum(axis=1) == 0].index
            #assign_vec = tmp_clust.idxmax(axis=1)
            #ridicolous cudf workaround
            assign_vec = self.interface.df.Series(
                tmp_clust.columns[
                self.interface.get_value(
                self.interface.num.argmax(
                self.interface.get_value(tmp_clust),axis=1))])
            #cudf workaround
            assign_vec.index = tmp_clust.index

            assign_vec.loc[noise] = -1

        assign_vec = self.interface.df.concat([ori_assign_vec,assign_vec],
                axis=0)

        ori_proj = proj.loc[ori_assign_vec.index]
        proj = proj.loc[assign_vec.index]
        proj2d = proj2d.loc[assign_vec.index]

        """ Plot 2-dimensional umap with the new data. """
        
        plotting.plot_map(
            self.interface.get_value(
                proj2d,
                pandas=True),
            self.interface.get_value(
                assign_vec,
                pandas=True),
            'proj_update_' +
            clu_name,
            self.outpath)
        
        """ Reformat class assignment vector. """

        if self.gpu:
            assign_vec = self.interface.df.Series([x.split('_')[-1] for x in self.interface.get_value(assign_vec)],
                index = assign_vec.index).astype(int)
            ori_assign_vec = self.interface.df.Series([x.split('_')[-1] for x in self.interface.get_value(ori_assign_vec)],
                index = ori_assign_vec.index).astype(int)
        else:    
            assign_vec = assign_vec.astype(str).apply(lambda x: x.split('_')[-1]).astype(int)
            ori_assign_vec = ori_assign_vec.astype(str).apply(lambda x: x.split('_')[-1]).astype(int)
        
        """ Calculate score change. """

        new_score = functions.calc_score(proj, assign_vec, self.score, self.metric_clu, self.interface)
        old_score = functions.calc_score(ori_proj, ori_assign_vec, self.score, self.metric_clu, self.interface) 

        score_delta = new_score - old_score
                
        logging.info('Old clustering score for class '+clu_name+' : {:.3f}'.format(old_score))
        logging.info('New clustering score for class '+clu_name+' : {:.3f}'.format(new_score))
        logging.info('Score delta : {:.3f}'.format(score_delta))

        if score_delta < -self.tolerance:
        
            """ Rebuild clusters. """

            if clu_name == self.root:
                logging.warning('Clustering score deteriorates below tolerance '+
                    'at the first level. This level will be skipped for now, but raccoon '+
                    'should be rerun from scratch.')
                #return None
            
            logging.info('Clustering score deteriorates below tolerance, '+
                    'clusters will be rebuilt.')

            obj = RecursiveClustering(self.interface.df.concat([self.ori_data.loc[ori_samples.index],self.data.loc[samples.index]]), 
                depth=clu_name.count('_'), name=clu_name+'u', score=self.score, metric_clu=self.metric_clu, 
                outpath=self.outpath, debug=self.debug, gpu=self.gpu, **self.kwargs)
            obj.recurse()

            return obj.clus_opt
       
        """ If clusters were not rebuilt, return updated assignment. """

        return functions.one_hot_encode(assign_vec, self.minclusize, clu_name,
                            self.interface)

    
    def find_and_update(self):
        """ Update the clusters by adding the new data and rebuilding
            them if needed.
        """

        new_clus = []

        to_check = [self.root]+list(self.clu.columns[self.interface.get_value(self.clu.sum()>0)])
        to_check = [x for x in to_check if x in self.paramdata.index and
                        self.paramdata['n_clusters'].loc[x]>1]

        while len(to_check)>0:
            tmp_clu = self.single_update(to_check[0])
            
            children = [x for x in self.ori_clu.columns if
                        x.startswith(to_check[0]) and x.count('_') == to_check[0].count('_')+1]
            offspring = [x for x in self.ori_clu.columns if
                        x.startswith(to_check[0]) and x.count('_') > to_check[0].count('_')]
            
            if tmp_clu is not None:
                
                new_clus.append(tmp_clu)

                if 'u' in tmp_clu.columns[0]:
                    
                    """ Plot homogeneity map if branch was rebuilt. """

                    comp1 = self.ori_clu[children]
                    comp2 = tmp_clu[[x for x in tmp_clu.columns 
                            if x.count('_') == children[0].count('_')]]#\
                            #.loc[comp1.index]

                    plotting.plot_homogeneity(
                        self.interface.get_value(
                            self.ori_clu[children],
                            pandas=True),
                        self.interface.get_value(
                            tmp_clu[[x for x in tmp_clu.columns
                                     if x.count('_') == children[0].count('_')]],
                            pandas=True),
                        'homogen' + to_check[0],
                        self.outpath)

            else:
                #if to_check[0]  == self.root:
                pass

            last_clu = to_check.pop(0)

            """ If the cluster was rebuilt, do not search its children. """
            
            #if last_clu != self.root and (tmp_clu is None or 'u' in tmp_clu.columns[0]):
            if tmp_clu is None or 'u' in tmp_clu.columns[0]:
                to_check = [x for x in to_check if x not in offspring]

        if new_clus != []:  
            self.new_clus = self.interface.df.concat(
                new_clus, axis=1)
            self.new_clus = self.new_clus[functions.sort_len_num(self.new_clus.columns)]
            self.new_clus =  self.new_clus.fillna(0).astype(int)

