
"""
Utility functions for RACCOON
(Recursive Algorithm for Coarse-to-fine Clustering OptimizatiON)
F. Comitani     @2018-2021
A. Maheshwari   @2019
"""

import os
import sys
import shutil
import warnings
import csv
import pickle

import logging

from scipy.stats import gaussian_kde
from scipy.stats import median_absolute_deviation as mad
from scipy.signal import argrelextrema

from raccoon.utils.plots import plot_violin


def _near_zero_var_drop(data, interface, thresh=0.99, type='variance'):
    """ Drop features with low variance/MAD based on a threshold after sorting them,
        converting to a cumulative function and keeping the 'thresh' % most variant features.

    Args:

        data (pandas dataframe): input pandas dataframe (samples as row, features as columns).
        interface (obj): CPU/GPU numeric functions interface.
        thresh (float): percentage threshold for the cumulative variance/MAD.
        type (string): measure of variability, to be chosen between
            variance ('variance') or median absolute deviation ('MAD').
    """

    if type == 'variance':
        v_val = data.var(axis=0).values
    elif type == 'MAD':
        v_val = data.apply(mad, axis=0).values

    cs = interface.df.Series(v_val).sort_values(ascending=False).cumsum()

    #remove = cs[cs > cs.iloc[-1]*thresh].index
    # return data.drop(data.columns[remove], axis=1)

    # check if order of columns matters
    keep = cs[cs <= cs.iloc[-1] * thresh].index
    # temorary workaround to cuDF bug
    # where we cannot slice with index (not iterable error)
    return data.iloc[:, interface.get_value(keep)]


def _drop_collinear(data, interface, thresh=0.75):
    """ Drop collinear features above the 'thresh' % of correlation.
        WARNING: very slow! Use tSVD instead!

    Args:

        data (pandas dataframe): input pandas dataframe (samples as row, features as columns).
        interface (obj): CPU/GPU numeric functions interface.
        thresh (float): percentage threshold for the correlation.
    """

    crmat = interface.df.DataFrame(
        interface.num.corrcoef(data.astype(float).T),
        columns=data.columns,
        index=data.columns)
    crmat.index.name = None
    crmat2 = crmat.where(
        interface.num.triu(interface.num.ones(crmat.shape),
            k=1).astype(interface.num.bool)).stack()
    crmat2 = crmat2.reset_index().sort_values(0, ascending=False)
    crmat2 = crmat2[crmat2[crmat2.columns[2]] > thresh]

    toremove = []
    while len(crmat2[crmat2.columns[2]]) > 0:
        a = crmat2[crmat2.columns[0]].iloc[0]
        b = crmat2[crmat2.columns[1]].iloc[0]
        meana = crmat.loc[a, crmat.columns.difference([a, b])].mean()
        meanb = crmat.loc[b, crmat.columns.difference([a, b])].mean()

        toremove.append([a, b][interface.num.argmax([meana, meanb])])

        crmat2 = crmat2[(crmat2[crmat2.columns[0]] != toremove[-1])
                        & (crmat2[crmat2.columns[1]] != toremove[-1])]

    return data.drop(toremove, axis=1)


def _drop_min_KDE(data, interface, type='variance'):
    """ Use kernel density estimation to guess the optimal cutoff for low-variance removal.

    Args:
        data (pandas dataframe): input pandas dataframe (samples as row, features as columns).
        interface (obj): CPU/GPU numeric functions interface.
        type (string): measure of variability, to be chosen between
            variance ('variance') or median absolute deviation ('MAD').

    """

    if type == 'variance':
        v_val = data.var(axis=0).values
    elif type == 'MAD':
        v_val = data.apply(mad, axis=0).values

    # x = interface.num.arange(interface.num.amin(v_val), interface.num.amax(v_val),
    #              (interface.num.amax(v_val)-interface.num.amin(v_val))/100)
    x = interface.num.linspace(
        interface.num.amin(v_val),
        interface.num.amax(v_val),
        100)
    kde = gaussian_kde(v_val, bw_method=None)
    y = kde.evaluate(x)

    imax = argrelextrema(y, interface.num.greater)[0]
    imin = argrelextrema(y, interface.num.less)[0]
    cutoff = None

    """ Take the last min before abs max. """

    absmax = interface.num.argmax(y[imax])

    if absmax > 0:
        cutoff = x[interface.num.amax(
            [xx for xx in imin if xx < imax[absmax]])]

    if cutoff is not None:
        cs = interface.df.Series(v_val, index=data.columns)
        remove = cs[cs < cutoff].index.values
        data = data.drop(remove, axis=1)

    return data


def _calc_RPD(mh, labs, interface, plot=True, name='rpd', path=""):
    """ Calculate and plot the relative pairwise distance (RPD) distribution for each cluster.
        See XXX for the definition.
        DEPRECATED: UNSTABLE, only works with cosine.

    Args:
        mh (pandas dataframe): dataframe containing reduced dimensionality data.
        labs (pandas series): clusters memebership for each sample.
        interface (obj): CPU/GPU numeric functions interface.
        plot (boolean): True to generate plot, saves the RPD values only otherwise.
        name (string): name of output violin plot .png file.

    Returns:
        vals (array of arrays of floats): each internal array represents the RPD values of the corresponding cluster #.

    """

    from sklearn.metrics.pairwise import cosine_similarity

    cosall = cosine_similarity(mh)
    if not isinstance(labs, interface.df.Series):
        labs = interface.df.Series(labs, index=mh.index)
    else:
        labs.index = mh.index

    csdf = interface.df.DataFrame(cosall, index=mh.index, columns=mh.index)
    csdf = csdf.apply(lambda x: 1 - x)

    lbvals = interface.set(labs.values)

    centroids = []
    for i in lbvals:
        centroids.append(mh.iloc[interface.num.where(labs == i)].mean())

    centroids = interface.df.DataFrame(
        centroids, index=lbvals, columns=mh.columns)
    coscen = cosine_similarity(centroids)
    coscen = interface.df.DataFrame(coscen, index=lbvals, columns=lbvals)
    coscen = coscen.apply(lambda x: 1 - x)
    #interface.num.fill_diagonal(coscen.values, 9999)

    vals = []
    for i in lbvals:
        if i != -1:

            matrix = csdf[labs[labs ==
                               i].index].loc[labs[labs == i].index].values

            siblings = lbvals
            siblings.remove(i)
            siblings.discard(-1)

            """ take only the upper triangle (excluding the diagonal)
                of the matrix to avoid duplicates. """

            vals.append([x * 1.0 / coscen[i].loc[list(siblings)].min() for x in
                         matrix[interface.num.triu_indices(matrix.shape[0], k=1)]])

    with open(os.path.join(path, 'raccoon_data/rinterface.df.pkl'), 'rb') as f:
        try:
            cur_maps = pickle.load(f)
        except EOFError:
            cur_maps = []

    for i in range(len(vals)):
        cur_maps.append([name + "_" + str(i), vals[i]])

    with open(os.path.join(path, 'raccoon_data/rinterface.df.pkl'), 'wb') as f:
        pickle.dump(cur_maps, f)

    if plot:
        plot_violin(vals, interface, name, path)

    return vals


def setup(outpath=None, chk=False, RPD=False):
    """ Set up folders that are written to during clustering,
    as well as a log file where all standard output is sent.
        If such folders are already present in the path, delete them.

    Args:
        outpath (string): path where output files will be saved.
        chk (bool): if true create checkpoints subdirectory
            (default False).
        RPD (bool): deprecated, if true created RPD distributions base pickle
            (default False).
    """

    """ Build folders and delete old data if present. """

    try:
        os.makedirs(os.path.join(outpath, 'raccoon_data'))
        if chk:
            os.makedirs(os.path.join(outpath, 'raccoon_data/chk'))
        os.makedirs(os.path.join(outpath, 'raccoon_plots'))
    except FileExistsError:
        warnings.warn('raccoon_data/raccoon_plots already found in path!')
        answer = None
        while answer not in ['y', 'yes', 'n', 'no']:
            answer = input(
                "Do you want to delete the old folders? [Y/N]  ").lower()
        if answer.startswith('y'):
            shutil.rmtree(os.path.join(outpath, 'raccoon_data'))
            os.makedirs(os.path.join(outpath, 'raccoon_data'))
            if chk:
                os.makedirs(os.path.join(outpath, 'raccoon_data/chk'))
            shutil.rmtree(os.path.join(outpath, 'raccoon_plots'))
            os.makedirs(os.path.join(outpath, 'raccoon_plots'))
        else:
            print('Please remove raccoon_data/plots manually or \
                   change output directory before proceeding!')
            sys.exit(1)

    """ Generate empty optimal paramaters table,
        to be written to at each iteration. """

    vals = ['name', 'n_samples', 'n_clusters',
        'dim', 'obj_function_score', 'n_neighbours',
        'cluster_parm', 'features_cutoff', 'metric_map',
        'metric_clust', 'norm', 'reassigned', 'seed']

    with open(os.path.join(outpath, 'raccoon_data/paramdata.csv'), 'w') as file:
        writer = csv.writer(file)
        writer.writerow(vals)
        file.close()

    """ Generate empty calc_RPD distributions pickle,
        to be written to at each iteration. """

    if RPD:
        with open(os.path.join(outpath, 'raccoon_data/rpd.pkl'), 'wb') as file:
            empty = []
            pickle.dump(empty, file)
            file.close()

    """ Configure log. """

    logname = 'raccoon_' + str(os.getpid()) + '.log'
    print('Log information will be saved to ' + logname)

    logging.basicConfig(
        level=logging.INFO,
        filename=os.path.join(
            outpath,
            logname),
        filemode="a+",
        format="%(asctime)-15s %(levelname)-8s %(message)s")
    logging.getLogger('matplotlib.font_manager').disabled = True


def sigmoid(x, interface, a=0, b=1):
    """ Sigmoid function

    Args:
        x (float): position at which to evaluate the function
        interface (obj): CPU/GPU numeric functions interface.
        a (float): center parameter
        b (float): slope parameter

    Returns:
        (float): sigmoid function evaluated at position x
    """

    return 1 / (1 + interface.num.exp((x - a) * b))


def loc_cat(labels, indices, supervised):
    """ Selects labels in
        supervised UMAP and transform them to categories.

    Args:
        indices (array-like): list of indices.
        supervised (bool): True if running superived UMAP.
    Returns:
        (Series): sliced labels series as categories if it exists.
    """

    if labels is not None and supervised:
        try:
            return labels.loc[indices].astype('category').cat.codes
        except BaseException:
            warnings.warn("Failed to subset labels.")
    return None

#def makeleaf_csv(filename, rowname):
#    
#    """ Replaces the 'leaf' column value in a given row
#        in the params csv file.

#    Args:
#        filename (string): path to file to modify.
#        rowname (string): name of the cluster (row) to modify.
#    """
    
#    with open(filename, 'rt') as infile,\
#         open(filename[:-4]+'_tmp.csv', 'wt') as outfile:
        
#        reader = csv.reader(infile)
#        writer = csv.writer(outfile)
#        for line in reader:
#            if line[0] == 'cluster ' + rowname:
#                writer.writerow(line[:-2]+['True',line[-1]])
#                break
#            else:
#                writer.writerow(line)
#        writer.writerows(reader)

#        infile.close()
#        outfile.close()

#        os.remove(filename)
#        os.rename(filename[:-4]+'_tmp.csv',filename)

