"""
==================================
// LICENSE:
// Copyright 2024 University of Sydney
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.
// ===============================
// AUTHOR       : Qinlin (Alistair) Gu, 
//                modified based on Dr. Clara Grazian's original R code.
// CREATE DATE  : 26/03/2024
// PURPOSE      : To perform the vABC algorithm using GPU acceleration.
// SPECIAL NOTES:
// ===============================
// Change History:
// 26/03/2024 - Qinlin (Alistair) Gu - Initial creation of the file.
==================================
"""

import cupy as cp
import pandas as pd
import numpy as np
from scipy.signal import convolve as sp_convolve
import os
import warnings
from tqdm import tqdm

# Filter out FutureWarnings in Pandas
warnings.filterwarnings("ignore", category=FutureWarning)

def extract_values(df):
    """
    Extracts the values from the given DataFrame.

    Args:
        df (pd.DataFrame): Input DataFrame.

    Returns:
        tuple: A tuple containing the extracted values.
            Each has a shape of (num_time_frame).
    """
    time_frame_size = cp.array(df.iloc[:, 0]).astype(cp.float16)
    Ti = cp.array(df.iloc[:, 1].values).astype(cp.float16)
    Cb = cp.array(df.iloc[:, 2].values).astype(cp.float16)

    return time_frame_size, Cb, Ti

def extract_TAC_chunks(df, index, chunk_size, num_voxel):
    """
    Extracts chunks of TAC (Time-Activity Curve) from the given DataFrame.
    Chunks are used so that only a proportion of the data is loaded into memory 
        at a time, preventing memory overflow.

    Args:
        df (pd.DataFrame): Input DataFrame.
        index (int): Starting index of the chunk.
        chunk_size (int): Size of each chunk.
        num_voxel (int): Number of voxels.

    Returns:
        cp.ndarray: Extracted TAC chunks, shaped as (num_time_frame, num_voxel).
    """
    df_column_size = df.shape[1]
    num_of_other_columns = 3 ## 3 columns are for time_frame_size, Cb, and Ti
    if num_voxel is None: ## When None, use all voxels
        num_voxel = df_column_size - num_of_other_columns
    chunk_end = min(index + chunk_size, df_column_size, num_voxel + num_of_other_columns)
    Ct = cp.array(df.iloc[:, index: chunk_end].values).astype(cp.float32)

    return Ct

def output_file_init(path_output_para, path_output_model, write_paras, output_compressed):
    """
    Initializes the output files for parameter and model data, so that results
        can be recorded in chunks.

    Args:
        path_output_para (str): Path to the parameter output file.
        path_output_model (str): Path to the model output file.
        write_paras (bool): flag indicating whether to write parameter posterior.
        output_compressed (bool): flag indicating whether to compress the output 
                                  posteriors (hdf5/csv). Note that the model 
                                  probability posterior is always stored as a csv
                                  as it is relatively small.
    """
    para_columns = ["Voxel_No", "Vb", "K_1", "k_2", 
                    "k_3", "k_4", "K_i", "model"]
    num_of_columns = len(para_columns)
    model_p_columns = ["Voxel_No", "model", "probability_of_model"]

    if write_paras:
        para_df = pd.DataFrame(columns = para_columns)
        para_df.iloc[:, 0] = para_df.iloc[:, 0].astype(np.int32)
        para_df.iloc[:, 1:num_of_columns-1] = para_df.iloc[:, 1:num_of_columns-1].astype(np.float32)
        para_df.iloc[:, -1] = para_df.iloc[:, -1].astype(np.str_)
        if output_compressed:
            para_df.to_hdf(path_output_para, 
                           key = "column_names", 
                           format='table', 
                           index = False, 
                           mode = "w", 
                           complevel = 9)
        else:
            para_df.to_csv(path_output_para, 
                           index = False, 
                           mode = "w", 
                           # compression = "xz"
                           )

    model_p_df = pd.DataFrame(columns = model_p_columns)
    model_p_df.to_csv( ## csv first, compression later, otherwise slow
        path_output_model.replace("h5", "csv"), 
        index = False, 
        mode = "w", 
        # compression = "xz"
        )

def output_dataframe(para, model_p, write_paras):
    """
    Prepares the output DataFrames for parameter and model data.

    Args:
        para (cp.ndarray): Parameter data. Contains Vb, alpha1, alpha2, theta1,
            theta2, and model values.
        model_p (cp.ndarray): Model data.
        write_paras (bool): flag indicating whether to write parameter posterior.

    Returns:
        tuple: A tuple containing the prepared DataFrames.
            para_df is the parameter posterior DataFrame. (K_1 etc.)
            model_p_df is the model probability posterior DataFrame.
    """
    if write_paras:
        para_df = pd.DataFrame(para)
        para_df[0] = para_df.iloc[:, 0].astype(int)

        ## We have alpha1 alpha2 theta1 theta2
        ## We need K_1 k_2 k_3 k_4

        Vb = para_df.iloc[:, 1]
        alpha1 = para_df.iloc[:, 2]
        alpha2 = para_df.iloc[:, 3]
        theta1 = para_df.iloc[:, 4]
        theta2 = para_df.iloc[:, 5]
        models = para_df.iloc[:, 6]

        K_1 = (theta1 + theta2) / (1 - Vb)
        k_2 = (theta1 * alpha1 + theta2 * alpha2) / (theta1 + theta2)
        k_4 = alpha1 * alpha2 / k_2
        k_3 = alpha1 + alpha2 - k_2 - k_4
        K_i = K_1 * k_3 / (k_2 + k_3)

        para_df.iloc[:, 2] = K_1
        para_df.iloc[:, 3] = k_2
        para_df.iloc[:, 4] = k_3
        para_df.iloc[:, 5] = k_4
        para_df.insert(6, "K_i", K_i)
        para_df.iloc[:, 7] = models

        para_df.iloc[:, -1] = para_df.iloc[:, -1].replace({0: 'k4 zero', 
                                                           1: 'k4 non-zero'})
    else:
        para_df = None

    model_p_df = pd.DataFrame(model_p)
    model_p_df[0] = model_p_df.iloc[:, 0].astype(int)
    model_p_df[2] = model_p_df.iloc[:, 2].astype(float)
    model_p_df.iloc[:, 2] = np.where(model_p_df.iloc[:, 1] == 1, 
                                     1 - model_p_df.iloc[:, 2], 
                                     model_p_df.iloc[:, 2])
    model_p_df.iloc[:, 1] = model_p_df.iloc[:, 1].replace({0: 'k4 zero', 
                                                           1: 'k4 non-zero'})

    return para_df, model_p_df

def write_csv_chunks(para_df, 
                     model_p_df, 
                     path_output_para, 
                     path_output_model, 
                     write_paras, 
                     output_compressed):
    """
    Writes the parameter and model data to CSV files in chunks.

    Args:
        para_df (pd.DataFrame): Parameter data.
        model_p_df (pd.DataFrame): Model data.
        path_output_para (str): Path to the parameter output file.
        path_output_model (str): Path to the model output file.
        write_paras (bool): flag indicating whether to write parameter posterior.
        output_compressed (bool): flag indicating whether to compress the output 
                                  posteriors (hdf5/csv). Note that the model 
                                  probability posterior is always stored as a csv
                                  as it is relatively small.
    """
    if write_paras:
        para_columns = ["Voxel_No", "Vb", "K_1", "k_2", 
                        "k_3", "k_4", "K_i", "model"]
        num_of_columns = len(para_columns)
        para_df.columns = para_columns

        unique_voxels = para_df.iloc[:, 0].unique()
        min_index = np.min(unique_voxels)
        max_index = np.max(unique_voxels)
        para_df.iloc[:, 0] = para_df.iloc[:, 0].astype(np.int32)
        para_df.iloc[:, 1:num_of_columns-1] = para_df.iloc[:, 1:num_of_columns-1].astype(np.float32)
        para_df.iloc[:, -1] = para_df.iloc[:, -1].astype(np.str_)
        if output_compressed:
            para_df.to_hdf(path_output_para, 
                        key = f"voxel_{min_index}_{max_index}", 
                        format='table', 
                        index = False, 
                        mode = 'a', 
                        complevel = 9, 
                        min_itemsize = {'model': 11})
        else:
            para_df.to_csv(
                path_output_para, 
                header = False, 
                index = False, 
                mode = "a", 
                # compression = "xz"
                )

    model_p_df.to_csv(
        path_output_model.replace("h5", "csv"), 
        header = False, 
        index = False, 
        mode = "a", 
        # compression = "xz"
        )
    
def compress_csv(path_output_model):
    """
    Compresses the model output CSV file to HDF5 format, when all are done

    Args:
        path_output_model (str): Path to the model output file.
    """
    path_output_model_csv = path_output_model.replace("h5", "csv")
    df = pd.read_csv(path_output_model_csv)
    df.iloc[:, 0] = df.iloc[:, 0].astype(np.int32)
    df.iloc[:, 1] = df.iloc[:, 1].astype(np.str_)
    df.iloc[:, 2] = df.iloc[:, 2].astype(np.float32)
    df.to_hdf(path_output_model, 
              key = "df", 
              index = False, 
              mode = "w", 
              complevel = 9)

    if os.path.exists(path_output_model_csv):
        os.remove(path_output_model_csv)

def cumconv(a, b, time_frame_size):
    """
    Performs cumulative convolution of two arrays.

    Args:
        a (cp.ndarray): First array.
        b (cp.ndarray): Second array.
        time_frame_size (cp.ndarray): Time frame size.

    Returns:
        cp.ndarray: Result of the cumulative convolution, shaped as 
            (1, num_prior_simulation_size, num_time_frame). The first dimension
            is added to match the num_voxels.
    """
    num_time_frame = b.shape[-1]
    a_cpu = a.get()
    b_cpu = b.get()
    ret = sp_convolve(a_cpu, b_cpu)[:, :, :num_time_frame]
    ## Only takes the first num_time_frame elements because of how the function
    ## works. This part is not very optimised. CuPy hasn't implemented SciPy's 
    ## convolve function yet. But it is parallelised, so it's not too bad.
    ret = cp.array(ret).astype(cp.float16)
    ret = ret * time_frame_size

    return ret

def generate_FDG_models(time_frame_size, Cb, Ca, Ti, paras):
    """
    Generates FDG (Fluorodeoxyglucose) model TACs using simulated priors.

    Args:
        time_frame_size (cp.ndarray): Time frame size.
        Cb (cp.ndarray): Cb values.
        Ca (cp.ndarray): Ca values.
        Ti (cp.ndarray): Ti values.
        paras (cp.ndarray): Parameter values. Contains Vb, alpha1, alpha2,
            theta1, theta2, and model values.

    Returns:
        cp.ndarray: FDG model TACs, shaped as the return value of cumconv.
    """
    Vb, alpha1, alpha2, theta1, theta2, model = [row for row in paras]
    Ct = cumconv((theta1 * cp.exp(-alpha1 * Ti) + theta2 * cp.exp(-alpha2 * Ti)), 
                 Ca, time_frame_size) + Vb * Cb

    return Ct

def calculate_results(M, par_mat, Ct, S, thresh, write_paras, 
                      model_0_prob_thres=0.5, vox_num_start=0):
    """
    Calculates the accepted simulations based on the given inputs.

    Args:
        M (cp.ndarray): FDG model TACs using the prior simulations.
            (num_vox, num_prior_simulation_size, num_time_frame)
        par_mat (cp.ndarray): Parameter matrix.
            (num_prior_simulation_size, num_variable)
        Ct (cp.ndarray): TAC chunks.
            (num_time_frame, num_vox)
        S (int): Prior simulation size. i.e. num_prior_simulation_size
        thresh (float): Threshold value for acceptance. Tunable.
        write_paras (bool): flag indicating whether to write parameter posterior.
        model_0_prob_thres (float): Threshold for model 0 probability. Tunable.
        vox_num_start (int): Starting voxel number. For batching purpose.

    Returns:
        tuple: A tuple containing the accepted parameter posteriors and model 
            probabilities.
    """
    num_vox = Ct.shape[-1]
    num_variable = 6

    voxel_numbers = cp.arange(num_vox) + vox_num_start

    Ct = Ct.T[:, None, :] ## (num_vox, 1, num_time_frame), 
                          ## second dimension for broadcasting
    errors = cp.sum(cp.abs(M - Ct), axis = -1) ## along time_frame axis
                                               ## (num_vox, num_prior_simulation_size)
    h = cp.quantile(errors, thresh, axis = -1) ## along num_prior_simulation_size axis
                                               ## (num_vox)
    accepted_mask = errors <= h[:, None] ## (num_vox, num_prior_simulation_size)
    accepted_size = int(cp.count_nonzero(accepted_mask[0]))
    ## This usually is fine but is risky
    ## Sometimes for special distance functions, there can be ties
    ## Leading to different accepted_size
    ## Making the vectorisation of the code not work

    ## was (num_prior_simulation_size, num_variable)
    par_mat_broadcast_shape = (num_vox, S, num_variable)
    par_mat = cp.broadcast_to(par_mat, par_mat_broadcast_shape)
    ## to repeat the par_mat for each voxel, for output purpose
    ## (num_vox, num_prior_simulation_size, num_variable)
    accepted_mask = cp.broadcast_to(accepted_mask[:, :, None], 
                                    par_mat_broadcast_shape)
    ## mask was (num_vox, num_prior_simulation_size)
    ## mask repeated for each variable
    ## (num_vox, num_prior_simulation_size, num_variable)
    accepted = par_mat[accepted_mask]
    accepted = accepted.reshape(num_vox, accepted_size, num_variable)
    ## reshaping needed because applying the mask will flatten the array
    ## (num_vox, accepted_size, num_variable)
    ## Errors will happen if accepted_size is different for different voxels
    ## Potential risky distance functions include those using ranks

    models = accepted[:, :, -1] ## (num_vox, accepted_size)
                                ## array of models accepted for each voxel
    percentage_zeros = cp.mean(models == 0, axis = -1) ## along accepted_size axis
    models = (percentage_zeros < model_0_prob_thres).astype(cp.int32)
    model_p = cp.column_stack((voxel_numbers, models, percentage_zeros))
    model_p = model_p.get()

    if write_paras:
        accepted = accepted.reshape(num_vox * accepted_size, num_variable)
        voxel_numbers = voxel_numbers.repeat(accepted_size)
        accepted = cp.column_stack((voxel_numbers, accepted))
        accepted = accepted.get()
    else:
        accepted = None

    return accepted, model_p

def vABC(num_voxel, path_data, path_output_para, path_output_model, par_mat, S, 
         thresh, model_0_prob_thres, write_paras, input_compressed=False, 
         output_compressed=False, chunk_size=25):
    """
    Performs the vABC (Variational Approximate Bayesian Computation) algorithm.

    Args:
        num_voxel (int): Number of voxels to process. If None, all voxels are
        path_data (str): Path to the input data file.
        path_output_para (str): Path to the parameter posterior output file.
        path_output_model (str): Path to the model probability posterior output file.
        par_mat (cp.ndarray): Parameter matrix, size (num_prior_simulation_size, num_variable).
        S (int): Size of the simulation. i.e. num_prior_simulation_size
        thresh (float): Threshold value for acceptance. Tunable.
        model_0_prob_thres (float): Threshold for model 0 probability. Tunable.
        write_paras (bool): flag indicating whether to write parameter posterior.
        input_compressed (bool): flag indicating whether the input data is compressed (hdf5/csv).
        output_compressed (bool): flag indicating whether to compress the output 
                                  posteriors (hdf5/csv). Note that the model 
                                  probability posterior is always stored as a csv
                                  as it is relatively small.
        chunk_size (int): Size of each chunk. Used to prevent memory overflow.
    """
    if input_compressed:
        df = pd.read_hdf(path_data, "df") ## alter, use read_csv(chunksize=)
    else:
        df = pd.read_csv(path_data)
    time_frame_size, Cb, Ti = extract_values(df)
    Ca = Cb ## as a part of our hypothesis

    time_frame_size = time_frame_size[None, None, :]
    Ca = Ca[None, None, :]
    Cb = Cb[None, None, :]
    Ti = Ti[None, None, :]
    ## shape (1, 1, num_time_frame)
    ## to match (num_vox, num_prior_simulation_size, num_time_frame)
    paras = par_mat.T[:, None, :, None]
    ## shape (num_variable, 1, num_prior_simulation_size, 1)
    ## to match (num_variable, num_vox, num_prior_simulation_size, num_time_frame)

    M = generate_FDG_models(time_frame_size, Cb, Ca, Ti, paras)

    index = 3 ## ignoring the first 3 columns
              ## which are for time_frame_size, Cb, and Ti
    df_column_size = df.shape[1] ## number of columns in the DataFrame

    output_file_init(path_output_para, path_output_model, write_paras, output_compressed)
    ## initialise the output files

    if num_voxel is None: ## When None, use all voxels
        num_voxel = df_column_size - 3

    # Calculate the number of iterations needed for the tqdm progress bar
    total_iterations = min(num_voxel, df_column_size - 3) / chunk_size
    total_iterations = int(total_iterations) if total_iterations.is_integer() else int(total_iterations) + 1

    for _ in tqdm(range(total_iterations)):
        ## batching to prevent memory overflow
        if index >= df_column_size or index >= num_voxel + 3:
            break

        Ct = extract_TAC_chunks(df, index, chunk_size, num_voxel)
        para, model_p = calculate_results(M, par_mat, Ct, S, thresh, write_paras, 
                                          model_0_prob_thres, index - 3)
        para_df, model_p_df = output_dataframe(para, model_p, write_paras)
        write_csv_chunks(para_df, model_p_df, path_output_para, path_output_model, 
                         write_paras, output_compressed)
        
        index += chunk_size

    if output_compressed:
        compress_csv(path_output_model)

def main():
    """
    Main function that executes the vABC algorithm.
    """

    """
    Input data file should have the shape:
    frame_length	Ti	        Cb	        0	        1	        2
    0	            0.133333333	0.632698	0.040760215	0.036601037	0.028408282
    0.166666667	    0.35	    140.5885	18.177458	17.420736	16.563547
    0.166666667	    0.516666667	17912.979	0	        0	        0
    0.166666667	    0.683333333	4444.976	8.17E-08	6.64E-08	4.08E-08
    0.166666667	    0.85	    2675.443	0.001335959	0.000509933	0.000515968

    The following columns are all TACs of the voxels.
    frame_length is the time frame size.
    Ti is the time after administration of the tracer.
    Cb is the whole blood input function.
        (Or equavalently Ca, the plasma input function)

    If input data is an HDF5 file, the key should be "df".
    """

    path_data = "../data/sample_data.csv"
    path_output_para = "parameters.csv"
    path_output_model = "model.csv"

    seed = 2024
    cp.random.seed(seed) ## for reproducibility

    chunk_size = 25 ## Adjust as needed, to prevent memory overflow

    S = 1*10**6 ## number of prior simulations
    thresh = 0.0001 ## threshold for acceptance
    model_0_prob_thres = 0.5 ## threshold for model 0 probability
    num_voxel = None ## number of voxels to process. If None, all voxels are
    write_paras = True ## flag indicating whether to write parameter posterior
    input_compressed = False ## flag indicating whether the input data is compressed (hdf5/csv)
    output_compressed = False
    ## flag indicating whether to compress the output 
    ## posteriors (hdf5/csv). Note that the model 
    ## probability posterior is always stored as a csv initially
    ## as it is relatively small, but can be compressed if needed.

    Vb = cp.random.uniform(0, 0.1, S)
    alpha1 = cp.random.uniform(0.0005, 0.015, S)
    alpha2 = cp.random.uniform(0.06, 0.6, S)
    theta1 = cp.random.uniform(0, 0.1, S)
    theta2 = cp.random.uniform(0, 0.1, S)
    model = cp.random.binomial(1, 0.5, S) ## 0 for k4 zero, 1 for k4 non-zero
    alpha1[model == 0] = 0 ## if k4 zero, alpha1 is 0
    ## Priors:
    ## Vb (cp.ndarray): Vb prior simulation values.
    ## alpha1 (cp.ndarray): alpha1 prior simulation values.
    ## alpha2 (cp.ndarray): alpha2 prior simulation values.
    ## theta1 (cp.ndarray): theta1 prior simulation values.
    ## theta2 (cp.ndarray): theta2 prior simulation values.
    ## model (cp.ndarray): Model prior simulation values.

    par_mat = cp.column_stack((Vb, alpha1, alpha2, theta1, theta2, model))
    ## stacked as input

    vABC(num_voxel, path_data, path_output_para, path_output_model, par_mat, 
         S, thresh, model_0_prob_thres, write_paras, input_compressed, 
         output_compressed, chunk_size)
    
if __name__ == "__main__":
    main()