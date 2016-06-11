import os, sys, time, pickle, gzip
import pandas as pd
import numpy as np
import multiprocessing as mp
pool_size = mp.cpu_count()

import xgboost as xgb

from datetime import datetime
from sklearn.cross_validation import train_test_split
from sklearn import linear_model, ensemble

from lib import conventions as conv

#===========================================
#   Trainer
#===========================================  
class trainer(object):

  def __init__(self, params):
    self.root     = params['root']
    self.stamp    = params['stamp']
    self.size     = params['size']
    self.x_cols   = params['x_cols']
    self.x_ranges = conv.get_range(params['size'], params['x_step'], params['x_inter'])
    self.y_ranges = conv.get_range(params['size'], params['y_step'], params['y_inter'])
    # extra_info
    self.data_cache = params['data_cache']
    self.loc_th_x = params['loc_th_x']
    self.loc_th_y = params['loc_th_y']
    self.en_preprocessing = params['en_preprocessing']

    # global variable for multi-thread
    global LOCATION, AVAIL_WDAYS, AVAIL_HOURS, POPULAR, GRID_CANDS
    if os.path.exists(self.data_cache):
      LOCATION, AVAIL_WDAYS, AVAIL_HOURS, POPULAR, GRID_CANDS = pickle.load(open(self.data_cache, 'rb'))
      LOCATION['x_min'] = LOCATION.x_mean - self.loc_th_x*LOCATION.x_std
      LOCATION['x_max'] = LOCATION.x_mean + self.loc_th_x*LOCATION.x_std
      LOCATION['y_min'] = LOCATION.y_mean - self.loc_th_y*LOCATION.y_std
      LOCATION['y_max'] = LOCATION.y_mean + self.loc_th_y*LOCATION.y_std
    

  #----------------------------------------
  #   Main
  #----------------------------------------
  def train(self, df_train, alg="skrf", params={}):
    mdl_path = "%s/models/%s" % (self.root, self.stamp)
    os.mkdir(mdl_path)
    print("[Train] start with params=%s, write models to %s @ %s" % (params, mdl_path, conv.now('full')))
    
    processes = []
    for x_idx, (x_min, x_max) in enumerate(self.x_ranges):
      x_min, x_max = conv.trim_range(x_min, x_max, self.size)
      df_row = df_train[(df_train.x >= x_min) & (df_train.x < x_max)]
      mp_pool = mp.Pool(pool_size)
      for y_idx, (y_min, y_max) in enumerate(self.y_ranges): 
        y_min, y_max = conv.trim_range(y_min, y_max, self.size)
        df_grid = df_row[(df_row.y >= y_min) & (df_row.y < y_max)]

        # preprocessing
        if self.en_preprocessing:
          df_grid, cols_extra = conv.df_preprocess(self.en_preprocessing, df_grid, x_idx, y_idx, LOCATION, AVAIL_WDAYS, AVAIL_HOURS, POPULAR, GRID_CANDS)
          X_train, y_train, _ = conv.df2sample(df_grid, self.x_cols+cols_extra)
        else:
          X_train, y_train, _ = conv.df2sample(df_grid, self.x_cols)

        # save model (can't stay in memory, too large)
        clf = self.get_alg(alg, params)
        mdl_name = "%s/models/%s/grid_model_x_%s_y_%s.pkl.gz" % (self.root, self.stamp, x_idx, y_idx)
        p = mp_pool.apply_async(save_model, (mdl_name, clf, X_train, y_train))
        processes.append(p)
        clf = None  # clear memory
        # prevent memory explode!
        while (len(processes) > 20): processes.pop(0).get()
      print("[Train] grid(%i,%i): %i samples / %i classes @ %s" % (x_idx, y_idx, len(y_train), len(set(y_train)), conv.now('full')))
      mp_pool.close()
    while processes: processes.pop(0).get()
    print("[Train] done @ %s" % conv.now('full'))
    
      

  #----------------------------------------
  #   Tasks
  #----------------------------------------
  def get_alg(self, alg, params):
    if alg == 'skrf':
      clf = ensemble.RandomForestClassifier(n_estimators=params.get('n_estimators', 150), max_depth=params.get('max_depth', 11), n_jobs=-1)
      # clf = ensemble.RandomForestClassifier(n_estimators=params.get('n_estimators', 300), max_depth=params.get('max_depth', 11), n_jobs=-1)
      # clf = ensemble.RandomForestClassifier(n_estimators=params.get('n_estimators', 500), max_depth=params.get('max_depth', 11), n_jobs=-1)
    elif alg == 'skgbc':
      clf = ensemble.GradientBoostingClassifier(n_estimators=params.get('n_estimators', 30), max_depth=params.get('max_depth', 5))
    elif alg == 'sklr':
      clf = linear_model.LogisticRegression(multi_class='multinomial', solver = 'lbfgs')
    elif alg == 'xgb':
      # https://github.com/dmlc/xgboost/blob/master/python-package/xgboost/sklearn.py
      clf = xgb.XGBClassifier(n_estimators=params.get('n_estimators', 30), max_depth=params.get('max_depth', 15), learning_rate=params.get('learning_rate', 0.15), objective="multi:softprob", silent=True)
    return clf
  

def save_model(mdl_name, clf, X_train, y_train):
  clf.fit(X_train, y_train)
  pickle.dump(clf, gzip.open(mdl_name, 'wb'))
