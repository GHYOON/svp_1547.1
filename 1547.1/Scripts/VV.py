"""
Copyright (c) 2018, Sandia National Labs, SunSpec Alliance and CanmetENERGY
All rights reserved.
Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:
Redistributions of source code must retain the above copyright notice, this
list of conditions and the following disclaimer.
Redistributions in binary form must reproduce the above copyright notice, this
list of conditions and the following disclaimer in the documentation and/or
other materials provided with the distribution.
Neither the names of the Sandia National Labs and SunSpec Alliance nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Questions can be directed to support@sunspec.org

"""

import sys
import os
import traceback
from svpelab import gridsim
from svpelab import loadsim
from svpelab import pvsim
from svpelab import das
from svpelab import der
from svpelab import hil
from svpelab import p1547
import script
from svpelab import result as rslt
from datetime import datetime, timedelta

import numpy as np
import collections
import cmath
import math


def volt_vars_mode(vv_curves, vv_response_time, pwr_lvls, v_ref_value):

    result = script.RESULT_FAIL
    daq = None
    v_nom = None
    grid = None
    pv = None
    eut = None
    chil = None
    result_summary = None
    dataset_filename = None

    try:
        cat = ts.param_value('eut.cat')
        cat2 = ts.param_value('eut.cat2')
        sink_power = ts.param_value('eut.sink_power')
        p_rated = ts.param_value('eut.p_rated')
        p_rated_prime = ts.param_value('eut.p_rated_prime')
        var_rated = ts.param_value('eut.var_rated')
        s_rated = ts.param_value('eut.s_rated')

        #absorb_enable = ts.param_value('eut.abs_enabled')
        # DC voltages
        v_in_nom = ts.param_value('eut.v_in_nom')
        #v_min_in = ts.param_value('eut.v_in_min')
        #v_max_in = ts.param_value('eut.v_in_max')

        # AC voltages
        v_nom = ts.param_value('eut.v_nom')
        v_low = ts.param_value('eut.v_low')
        v_high = ts.param_value('eut.v_high')
        p_min = ts.param_value('eut.p_min')
        p_min_prime = ts.param_value('eut.p_min_prime')
        phases = ts.param_value('eut.phases')

        """
        A separate module has been create for the 1547.1 Standard
        """
        lib_1547 = p1547.module_1547(ts=ts, aif='VV')
        ts.log_debug("1547.1 Library configured for %s" % lib_1547.get_test_name())

        # result params
        result_params = lib_1547.get_rslt_param_plot()

        '''
        a) Connect the EUT according to the instructions and specifications provided by the manufacturer.
        '''

        # initialize HIL environment, if necessary
        chil = hil.hil_init(ts)
        if chil is not None:
            chil.config()

        # pv simulator is initialized with test parameters and enabled
        pv = pvsim.pvsim_init(ts)
        if pv is not None:
            pv.power_set(p_rated)
            pv.power_on()  # Turn on DC so the EUT can be initialized

        # DAS soft channels
        # TODO : add to library 1547
        das_points = {'sc': ('Q_TARGET', 'Q_TARGET_MIN', 'Q_TARGET_MAX', 'Q_MEAS', 'V_TARGET', 'V_MEAS', 'event')}

        # initialize data acquisition system
        daq = das.das_init(ts, sc_points=das_points['sc'])

        daq.sc['V_TARGET'] = v_nom
        daq.sc['Q_TARGET'] = 100
        daq.sc['Q_TARGET_MIN'] = 100
        daq.sc['Q_TARGET_MAX'] = 100
        daq.sc['event'] = 'None'

        ts.log('DAS device: %s' % daq.info())

        '''
        b) Set all voltage trip parameters to the widest range of adjustability.  Disable all reactive/active power
        control functions.
        '''

        eut = der.der_init(ts)
        if eut is not None:
            eut.config()
            ts.log_debug(eut.measurements())

            eut.volt_var(params={'Ena': False})
            eut.volt_watt(params={'Ena': False})
            eut.fixed_pf(params={'Ena': False})
            ts.log_debug('Voltage trip parameters set to the widest range: v_min: {0} V, '
                         'v_max: {1} V'.format(v_low, v_high))
            try:
                eut.vrt_stay_connected_high(params={'Ena': True, 'ActCrv': 0, 'Tms1': 3000,
                                                    'V1': v_high, 'Tms2': 0.16, 'V2': v_high})
            except Exception, e:
                ts.log_error('Could not set VRT Stay Connected High curve. %s' % e)
            try:
                eut.vrt_stay_connected_low(params={'Ena': True, 'ActCrv': 0, 'Tms1': 3000,
                                                   'V1': v_low, 'Tms2': 0.16, 'V2': v_low})
            except Exception, e:
                ts.log_error('Could not set VRT Stay Connected Low curve. %s' % e)
        else:
            ts.log_debug('Set L/HVRT and trip parameters set to the widest range of adjustability possible.')

        # Special considerations for CHIL ASGC/Typhoon startup
        if chil is not None:
            inv_power = eut.measurements().get('W')
            timeout = 120.
            if inv_power <= p_rated * 0.85:
                pv.irradiance_set(995)  # Perturb the pv slightly to start the inverter
                ts.sleep(3)
                eut.connect(params={'Conn': True})
            while inv_power <= p_rated * 0.85 and timeout >= 0:
                ts.log('Inverter power is at %0.1f. Waiting up to %s more seconds or until EUT starts...' %
                       (inv_power, timeout))
                ts.sleep(1)
                timeout -= 1
                inv_power = eut.measurements().get('W')
                if timeout == 0:
                    result = script.RESULT_FAIL
                    raise der.DERError('Inverter did not start.')
            ts.log('Waiting for EUT to ramp up')
            ts.sleep(8)
            ts.log_debug('DAS data_read(): %s' % daq.data_read())

        '''
        c) Set all AC test source parameters to the nominal operating voltage and frequency.
        '''
        grid = gridsim.gridsim_init(ts)  # Turn on AC so the EUT can be initialized
        if grid is not None:
            grid.voltage(v_nom)

        # open result summary file
        result_summary_filename = 'result_summary.csv'
        result_summary = open(ts.result_file_path(result_summary_filename), 'a+')
        ts.result_file(result_summary_filename)
        result_summary.write(lib_1547.get_rslt_sum_col_name())

        # STD_CHANGE Typo with step U. - Out of order
        '''
        d) Adjust the EUT's available active power to Prated. For an EUT with an input voltage range, set the input
        voltage to Vin_nom. The EUT may limit active power throughout the test to meet reactive power requirements.
        For an EUT with an input voltage range.
        '''
        ts.log('%s %s' % (p_rated, v_in_nom))
        ts.log('%s %s' % (type(p_rated), type(v_in_nom)))

        if pv is not None:
            pv.iv_curve_config(pmp=p_rated, vmp=v_in_nom)
            pv.irradiance_set(1000.)


        '''
        dd) Repeat steps e) through dd) for characteristics 2 and 3.
        '''
        for vv_curve in vv_curves:
            ts.log('Starting test with characteristic curve %s' % (vv_curve))
            v_pairs = lib_1547.get_params(curve=vv_curve)
            '''
            d2) Set EUT volt-var parameters to the values specified by Characteristic 1.
            All other function should be turned off. Turn off the autonomously adjusting reference voltage.
            '''
            if eut is not None:
                # Activate volt-var function with following parameters
                # SunSpec convention is to use percentages for V and Q points.
                vv_curve_params = {'v': [v_pairs['V1']*(100/v_nom), v_pairs['V2']*(100/v_nom),
                                         v_pairs['V3']*(100/v_nom), v_pairs['V4']*(100/v_nom)],
                                   'var': [v_pairs['Q1']*(100/var_rated),
                                           v_pairs['Q2']*(100/var_rated),
                                           v_pairs['Q3']*(100/var_rated),
                                           v_pairs['Q4']*(100/var_rated)]}
                ts.log_debug('Sending VV points: %s' % vv_curve_params)
                eut.volt_var(params={'Ena': True, 'curve': vv_curve_params})

                # ASK @Jay about this loop. Necessary here ? Could go inside your driver...

                for i in range(10):
                    if not eut.volt_var()['Ena']:
                        ts.log_error('EUT VV Enable register not set to True. Trying again...')
                        eut.volt_var(params={'Ena': True})
                        ts.sleep(1)
                    else:
                        break
                # TODO autonomous vref adjustment to be included
                # eut.autonomous_vref_adjustment(params={'Ena': False})

                '''
                e) Verify volt-var mode is reported as active and that the correct characteristic is reported.
                '''
                ts.log_debug('Initial EUT VV settings are %s' % eut.volt_var())

            '''
            cc) Repeat test steps d) through cc) at EUT power set at 20% and 66% of rated power.
            '''
            for power in pwr_lvls:
                if pv is not None:
                    pv_power_setting = (p_rated * power)
                    pv.iv_curve_config(pmp=pv_power_setting, vmp=v_in_nom)
                    pv.irradiance_set(1000.)

                # Special considerations for CHIL ASGC/Typhoon startup #
                if chil is not None:
                    inv_power = eut.measurements().get('W')
                    timeout = 120.
                    if inv_power <= pv_power_setting * 0.85:
                        pv.irradiance_set(995)  # Perturb the pv slightly to start the inverter
                        ts.sleep(3)
                        eut.connect(params={'Conn': True})
                    while inv_power <= pv_power_setting * 0.85 and timeout >= 0:
                        ts.log('Inverter power is at %0.1f. Waiting up to %s more seconds or until EUT starts...' %
                               (inv_power, timeout))
                        ts.sleep(1)
                        timeout -= 1
                        inv_power = eut.measurements().get('W')
                        if timeout == 0:
                            result = script.RESULT_FAIL
                            raise der.DERError('Inverter did not start.')
                    ts.log('Waiting for EUT to ramp up')
                    ts.sleep(8)

                '''
                bb) Repeat test steps e) through bb) with Vref set to 1.05*VN and 0.95*VN, respectively.
                '''
                for v_ref in v_ref_value:
                    ts.log('Setting v_ref at %s %% of v_nom' % (int(v_ref*100)))
                    v_steps_dict = collections.OrderedDict()
                    a_v = lib_1547.MSA_V * 1.5

                    # Capacitive test
                    v_steps_dict['Step F'] = v_pairs['V3'] - a_v
                    v_steps_dict['Step G'] = v_pairs['V3'] + a_v
                    v_steps_dict['Step H'] = (v_pairs['V3'] + v_pairs['V4']) / 2

                    '''
                    i) If V4 is less than VH, step the AC test source voltage to av below V4, else skip to step l).
                    l) Begin the return to VRef. If V4 is less than VH, step the AC test source voltage to av above V4,
                       else skip to step n).
                    '''
                    if v_pairs['V4'] < v_high:
                        v_steps_dict['Step I'] = v_pairs['V4'] - a_v
                        v_steps_dict['Step J'] = v_pairs['V4'] + a_v
                        v_steps_dict['Step K'] = v_high - a_v
                        v_steps_dict['Step L'] = v_pairs['V4'] + a_v
                        v_steps_dict['Step M'] = (v_pairs['V3'] + v_pairs['V4']) / 2
                    v_steps_dict['Step N'] = v_pairs['V3'] + a_v
                    v_steps_dict['Step O'] = v_pairs['V3'] - a_v
                    v_steps_dict['Step P'] = v_ref*v_nom

                    # Inductive test
                    v_steps_dict['Step Q'] = v_pairs['V2'] + a_v
                    v_steps_dict['Step R'] = v_pairs['V2'] - a_v
                    v_steps_dict['Step S'] = (v_pairs['V1'] + v_pairs['V2']) / 2

                    '''
                    t) If V1 is greater than VL, step the AC test source voltage to av above V1, else skip to step x).
                    '''
                    if v_pairs['V1'] > v_low:
                        v_steps_dict['Step T'] = v_pairs['V1'] + a_v
                        v_steps_dict['Step U'] = v_pairs['V1'] - a_v
                        v_steps_dict['Step V'] = v_low + a_v
                        v_steps_dict['Step W'] = v_pairs['V1'] + a_v
                        v_steps_dict['Step X'] = (v_pairs['V1'] + v_pairs['V2']) / 2
                    v_steps_dict['Step Y'] = v_pairs['V2'] - a_v
                    v_steps_dict['Step Z'] = v_pairs['V2'] + a_v
                    v_steps_dict['Step aa'] = v_ref*v_nom

                    for step, voltage in v_steps_dict.iteritems():
                        v_steps_dict.update({step: round(voltage, 2)})
                        if voltage > v_high:
                            v_steps_dict.update({step: v_high})
                        elif voltage < v_low:
                            v_steps_dict.update({step: v_low})

                    dataset_filename = 'VV_%s_PWR_%d_vref_%d' % (vv_curve, power * 100, v_ref*100)
                    ts.log('------------{}------------'.format(dataset_filename))
                    # Start the data acquisition systems
                    daq.data_capture(True)

                    for step_label, v_step in v_steps_dict.iteritems():
                        ts.log('Voltage step: setting Grid simulator voltage to %s (%s)' % (v_step, step_label))
                        q_initial = lib_1547.get_initial(daq=daq, step=step_label)
                        if grid is not None:
                            grid.voltage(v_step)
                        q_v_analysis = lib_1547.criteria(   daq = daq,
                                                            tr = vv_response_time[vv_curve],
                                                            step=step_label,
                                                            initial_value=q_initial,
                                                            curve=vv_curve,
                                                            pwr_lvl=power,
                                                            target=v_step)

                        result_summary.write(lib_1547.write_rslt_sum(analysis=q_v_analysis, step=step_label,
                                                                filename=dataset_filename))

                    ts.log('Sampling complete')
                    dataset_filename = dataset_filename + ".csv"
                    daq.data_capture(False)
                    ds = daq.data_capture_dataset()
                    ts.log('Saving file: %s' % dataset_filename)
                    ds.to_csv(ts.result_file_path(dataset_filename))
                    result_params['plot.title'] = dataset_filename.split('.csv')[0]
                    ts.result_file(dataset_filename, params=result_params)
                    result = script.RESULT_COMPLETE

    except script.ScriptFail, e:
        reason = str(e)
        if reason:
            ts.log_error(reason)

    except Exception as e:
        if dataset_filename is not None:
            dataset_filename = dataset_filename + ".csv"
            daq.data_capture(False)
            ds = daq.data_capture_dataset()
            ts.log('Saving file: %s' % dataset_filename)
            ds.to_csv(ts.result_file_path(dataset_filename))
            result_params['plot.title'] = dataset_filename.split('.csv')[0]
            ts.result_file(dataset_filename, params=result_params)
        ts.log_error('Test script exception: %s' % traceback.format_exc())

    finally:
        if daq is not None:
            daq.close()
        if pv is not None:
            pv.close()
        if grid is not None:
            if v_nom is not None:
                grid.voltage(v_nom)
            grid.close()
        if chil is not None:
            chil.close()
        if eut is not None:
            eut.volt_var(params={'Ena': False})
            eut.close()
        if result_summary is not None:
            result_summary.close()

    return result


def volt_vars_mode_vref_test():

    return 1

def volt_var_mode_imbalanced_grid(imbalance_resp, vv_curves, vv_response_time):

    result = script.RESULT_FAIL
    daq = None
    v_nom = None
    p_rated = None
    grid = None
    pv = None
    eut = None
    chil = None
    result_summary = None
    dataset_filename = None

    try:
        #cat = ts.param_value('eut.cat')
        #cat2 = ts.param_value('eut.cat2')
        #sink_power = ts.param_value('eut.sink_power')
        p_rated = ts.param_value('eut.p_rated')
        #p_rated_prime = ts.param_value('eut.p_rated_prime')
        var_rated = ts.param_value('eut.var_rated')
        s_rated = ts.param_value('eut.s_rated')

        #absorb_enable = ts.param_value('eut.abs_enabled')

        # DC voltages
        v_in_nom = ts.param_value('eut.v_in_nom')
        #v_min_in = ts.param_value('eut.v_in_min')
        #v_max_in = ts.param_value('eut.v_in_max')

        # AC voltages
        v_nom = ts.param_value('eut.v_nom')
        v_min = ts.param_value('eut.v_low')
        v_max = ts.param_value('eut.v_high')
        p_min = ts.param_value('eut.p_min')
        p_min_prime = ts.param_value('eut.p_min_prime')
        phases = ts.param_value('eut.phases')
        pf_response_time = ts.param_value('vv.test_imbalanced_t_r')

        # Pass/fail accuracies
        pf_msa = ts.param_value('eut.pf_msa')
        # According to Table 3-Minimum requirements for manufacturers stated measured and calculated accuracy
        MSA_Q = 0.05 * s_rated
        MSA_P = 0.05 * s_rated
        MSA_V = 0.01 * v_nom

        imbalance_fix = ts.param_value('vv.imbalance_fix')

        """
        A separate module has been create for the 1547.1 Standard
        """
        lib_1547 = p1547.module_1547(ts=ts, aif='VV', imbalance_angle_fix=imbalance_fix)
        ts.log_debug('1547.1 Library configured for %s' % lib_1547.get_test_name())

        # Get the rslt parameters for plot
        result_params = lib_1547.get_rslt_param_plot()

        '''
        a) Connect the EUT according to the instructions and specifications provided by the manufacturer.
        '''
        # initialize HIL environment, if necessary
        chil = hil.hil_init(ts)
        if chil is not None:
            chil.config()

        # grid simulator is initialized with test parameters and enabled
        grid = gridsim.gridsim_init(ts)  # Turn on AC so the EUT can be initialized
        if grid is not None:
            grid.voltage(v_nom)

        # pv simulator is initialized with test parameters and enabled
        pv = pvsim.pvsim_init(ts)
        pv.power_set(p_rated)
        pv.power_on()  # Turn on DC so the EUT can be initialized

        # DAS soft channels
        # TODO : add to library 1547
        das_points = {'sc': ('Q_TARGET', 'Q_TARGET_MIN', 'Q_TARGET_MAX', 'Q_MEAS', 'V_TARGET', 'V_MEAS', 'event')}

        # initialize data acquisition system
        daq = das.das_init(ts, sc_points=das_points['sc'])
        if daq is not None:
            daq.sc['Q_TARGET'] = 100
            daq.sc['Q_TARGET_MIN'] = 100
            daq.sc['Q_TARGET_MAX'] = 100
            daq.sc['V_TARGET'] = v_nom
            daq.sc['event'] = 'None'
            ts.log('DAS device: %s' % daq.info())

        '''
        b) Set all voltage trip parameters to the widest range of adjustability. Disable all reactive/active power
        control functions.
        '''

        '''
        c) Set all AC test source parameters to the nominal operating voltage and frequency.
        '''
        if grid is not None:
            grid.voltage(v_nom)

        # open result summary file
        result_summary_filename = 'result_summary.csv'
        result_summary = open(ts.result_file_path(result_summary_filename), 'a+')
        ts.result_file(result_summary_filename)

        result_summary.write(lib_1547.get_rslt_sum_col_name())

        '''
         d) Adjust the EUT's available active power to Prated. For an EUT with an input voltage range, set the input
        voltage to Vin_nom.
        '''

        if pv is not None:
            pv.iv_curve_config(pmp=p_rated, vmp=v_in_nom)
            pv.irradiance_set(1000.)

        '''
        h) Once steady state is reached, begin the adjustment of phase voltages.
        '''

        """
        Test start
        """

        for imbalance_response in imbalance_resp:
            for vv_curve in vv_curves:

                '''
                 e) Set EUT volt-watt parameters to the values specified by Characteristic 1. All other function be turned off.
                 '''

                v_pairs = lib_1547.get_params(curve=vv_curve)

                # it is assumed the EUT is on
                eut = der.der_init(ts)
                if eut is not None:
                    vv_curve_params = {'v': [v_pairs['V1']*(100/v_nom), v_pairs['V2']*(100/v_nom),
                                             v_pairs['V3']*(100/v_nom), v_pairs['V4']*(100/v_nom)],
                                       'q': [v_pairs['Q1']*(100/var_rated), v_pairs['Q2']*(100/var_rated),
                                             v_pairs['Q3']*(100/var_rated), v_pairs['Q4']*(100/var_rated)],
                                       'DeptRef': 'Q_MAX_PCT'}
                    ts.log_debug('Sending VV points: %s' % vv_curve_params)
                    eut.volt_var(params={'Ena': True, 'curve': vv_curve_params})

                    # ASK @Jay about this loop. Necessary here ? Could go inside your driver...

                    for i in range(10):
                        if not eut.volt_var()['Ena']:
                            ts.log_error('EUT VV Enable register not set to True. Trying again...')
                            eut.volt_var(params={'Ena': True})
                            ts.sleep(1)
                        else:
                            break
                    # TODO autonomous vref adjustment to be included
                    # eut.autonomous_vref_adjustment(params={'Ena': False})

                '''
                f) Verify volt-var mode is reported as active and that the correct characteristic is reported.
                '''
                if eut is not None:
                    ts.log_debug('Initial EUT VV settings are %s' % eut.volt_var())
                ts.log_debug('curve points:  %s' % v_pairs)

                '''
                g) Wait for steady state to be reached.
    
                Every time a parameter is stepped or ramped, measure and record the time domain current and voltage
                response for at least 4 times the maximum expected response time after the stimulus, and measure or
                derive, active power, apparent power, reactive power, and power factor.
                '''
                """
                Test start
                """
                step = 'Step G'
                daq.sc['event'] = step
                daq.data_sample()
                ts.log('Wait for steady state to be reached')
                ts.sleep(4 * vv_response_time[vv_curve])
                ts.log(imbalance_resp)

                ts.log('Starting imbalance test with VV mode at %s' % (imbalance_response))

                if imbalance_fix == "Yes":
                    dataset_filename = 'VV_IMB_%s_FIX' % (imbalance_response)
                else:
                    dataset_filename = 'VV_IMB_%s' % (imbalance_response)
                ts.log('------------{}------------'.format(dataset_filename))
                # Start the data acquisition systems
                daq.data_capture(True)

                '''
                h) For multiphase units, step the AC test source voltage to Case A from Table 24.
                '''
                if grid is not None:
                    step = 'Step H'
                    ts.log('Voltage step: setting Grid simulator to case A (IEEE 1547.1-Table 24)(%s)' % step)
                    q_initial = lib_1547.get_initial(daq=daq, step=step)
                    lib_1547.set_grid_asymmetric(grid=grid, case='case_a')
                    q_v_analysis = lib_1547.criteria(   daq=daq,
                                                        tr=vv_response_time[vv_curve],
                                                        step=step,
                                                        initial_value=q_initial,
                                                        curve=vv_curve)

                    result_summary.write(lib_1547.write_rslt_sum(analysis=q_v_analysis, step=step,
                                                                 filename=dataset_filename))

                '''
                w) For multiphase units, step the AC test source voltage to VN.
                '''
                if grid is not None:
                    # STD_CHANGE : This step is not following order
                    step = 'Step W'
                    ts.log('Voltage step: setting Grid simulator voltage to %s (%s)' % (v_nom, step))
                    q_initial = lib_1547.get_initial(daq=daq, step=step)
                    grid.voltage(v_nom)
                    q_v_analysis = lib_1547.criteria(daq=daq,
                                                     tr=vv_response_time[vv_curve],
                                                     step=step,
                                                     initial_value=q_initial,
                                                     curve=vv_curve,
                                                     target=v_nom)
                    result_summary.write(lib_1547.write_rslt_sum(analysis=q_v_analysis, step=step,
                                                                 filename=dataset_filename))

                """
                i) For multiphase units, step the AC test source voltage to Case B from Table 24.
                """
                if grid is not None:
                    step = 'Step I'
                    ts.log('Voltage step: setting Grid simulator to case B (IEEE 1547.1-Table 24)(%s)' % step)
                    q_initial = lib_1547.get_initial(daq=daq, step=step)
                    lib_1547.set_grid_asymmetric(grid=grid, case='case_b')
                    q_v_analysis = lib_1547.criteria(   daq=daq,
                                                        tr=vv_response_time[vv_curve],
                                                        step=step,
                                                        initial_value=q_initial,
                                                        curve=vv_curve
                                                        )
                    result_summary.write(lib_1547.write_rslt_sum(analysis=q_v_analysis, step=step,
                                                                 filename=dataset_filename))

                """
                j) For multiphase units, step the AC test source voltage to VN
                """
                if grid is not None:
                    ts.log('Voltage step: setting Grid simulator voltage to %s (%s)' % (v_nom, step))
                    step = 'Step J'
                    q_initial = lib_1547.get_initial(daq=daq, step=step)
                    grid.voltage(v_nom)
                    q_v_analysis = lib_1547.criteria(   daq=daq,
                                                        tr=vv_response_time[vv_curve],
                                                        step=step,
                                                        initial_value=q_initial,
                                                        curve=vv_curve,
                                                        target=v_nom)
                    result_summary.write(lib_1547.write_rslt_sum(analysis=q_v_analysis, step=step,
                                                                 filename=dataset_filename))


                ts.log('Sampling complete')
                dataset_filename = dataset_filename + ".csv"
                daq.data_capture(False)
                ds = daq.data_capture_dataset()
                ts.log('Saving file: %s' % dataset_filename)
                ds.to_csv(ts.result_file_path(dataset_filename))
                result_params['plot.title'] = dataset_filename.split('.csv')[0]
                ts.result_file(dataset_filename, params=result_params)
                result = script.RESULT_COMPLETE

    except script.ScriptFail, e:
        reason = str(e)
        if reason:
            ts.log_error(reason)


    except Exception as e:

        if dataset_filename is not None:
            dataset_filename = dataset_filename + ".csv"
            daq.data_capture(False)
            ds = daq.data_capture_dataset()
            ts.log('Saving file: %s' % dataset_filename)
            ds.to_csv(ts.result_file_path(dataset_filename))
            result_params['plot.title'] = dataset_filename.split('.csv')[0]
            ts.result_file(dataset_filename, params=result_params)

        raise

    finally:
        if daq is not None:
            daq.close()
        if pv is not None:
            if p_rated is not None:
                pv.power_set(p_rated)
            pv.close()
        if grid is not None:
            if v_nom is not None:
                grid.voltage(v_nom)
            grid.close()
        if chil is not None:
            chil.close()
        if eut is not None:
            eut.volt_var(params={'Ena': False})
            eut.volt_watt(params={'Ena': False})
            eut.close()
        if result_summary is not None:
            result_summary.close()

    return result

def test_run():

    result = script.RESULT_FAIL

    try:
        """
        Configuration
        """

        mode = ts.param_value('vv.mode')

        """
        Test Configuration
        """
        # list of active tests
        vv_curves = []
        vv_response_time = [0, 0, 0, 0]

        if mode == 'Vref-test':
            vv_curves['characteristic 1'] = 1
            vv_response_time[1] = ts.param_value('vv.test_1_t_r')
            irr = '100%'
            vref = '100%'
            result = volt_vars_mode_vref_test(vv_curves=vv_curves, vv_response_time=vv_response_time, pwr_lvls=pwr_lvls)

        # Section 5.14.6
        if mode == 'Imbalanced grid':
            if ts.param_value('eut.imbalance_resp') == 'EUT response to the individual phase voltages':
                imbalance_resp = ['INDIVIDUAL_PHASES_VOLTAGES']
            elif ts.param_value('eut.imbalance_resp') == 'EUT response to the average of the three-phase effective (RMS)':
                imbalance_resp = ['AVG_3PH_RMS']
            else:  # 'EUT response to the positive sequence of voltages'
                imbalance_resp = ['POSITIVE_SEQUENCE_VOLTAGES']

            vv_curves.append(1)
            vv_response_time[1] = ts.param_value('vv.test_1_t_r')

            result = volt_var_mode_imbalanced_grid(imbalance_resp=imbalance_resp,
                                                   vv_curves=vv_curves,
                                                   vv_response_time=vv_response_time )

        # Normal volt-var test (Section 5.14.4)
        else:
            irr = ts.param_value('vv.irr')
            vref = ts.param_value('vv.vref')
            if ts.param_value('vv.test_1') == 'Enabled':
                vv_curves.append(1)
                vv_response_time[1] = ts.param_value('vv.test_1_t_r')
            if ts.param_value('vv.test_2') == 'Enabled':
                vv_curves.append(2)
                vv_response_time[2] = ts.param_value('vv.test_2_t_r')
            if ts.param_value('vv.test_3') == 'Enabled':
                vv_curves.append(3)
                vv_response_time[3] = ts.param_value('vv.test_3_t_r')

            # List of power level for tests
            if irr == '20%':
                pwr_lvls = [0.20]
            elif irr == '66%':
                pwr_lvls = [0.66]
            elif irr == '100%':
                pwr_lvls = [1.]
            else:
                pwr_lvls = [1., 0.66, 0.20]

            if vref == '95%':
                v_ref_value = [0.95]
            elif vref == '105%':
                v_ref_value = [1.05]
            elif vref == '100%':
                v_ref_value = [1.]
            else:
                v_ref_value = [1, 0.95, 1.05]

            result = volt_vars_mode(vv_curves=vv_curves, vv_response_time=vv_response_time,
                                    pwr_lvls=pwr_lvls, v_ref_value=v_ref_value)

    except script.ScriptFail, e:
        reason = str(e)
        if reason:
            ts.log_error(reason)

    finally:
        # create result workbook
        excelfile = ts.config_name() + '.xlsx'
        rslt.result_workbook(excelfile, ts.results_dir(), ts.result_dir())
        ts.result_file(excelfile)

    return result


def run(test_script):
    try:
        global ts
        ts = test_script
        rc = 0
        result = script.RESULT_COMPLETE

        ts.log_debug('')
        ts.log_debug('**************  Starting %s  **************' % (ts.config_name()))
        ts.log_debug('Script: %s %s' % (ts.name, ts.info.version))
        ts.log_active_params()

        # ts.svp_version(required='1.5.3')
        ts.svp_version(required='1.5.8')

        result = test_run()
        ts.result(result)
        if result == script.RESULT_FAIL:
            rc = 1

    except Exception, e:
        ts.log_error('Test script exception: %s' % traceback.format_exc())
        rc = 1

    sys.exit(rc)


info = script.ScriptInfo(name=os.path.basename(__file__), run=run, version='1.2.0')

# VV test parameters
info.param_group('vv', label='Test Parameters')
info.param('vv.mode', label='Volt-Var mode', default='Normal', values=['Normal', 'Vref-test', 'Imbalanced grid'])
info.param('vv.test_1', label='Characteristic 1 curve', default='Enabled', values=['Disabled', 'Enabled'],
           active='vv.mode', active_value=['Normal', 'Imbalanced grid'])
info.param('vv.test_1_t_r', label='Response time (s) for curve 1', default=10.0,
           active='vv.test_1', active_value=['Enabled'])
info.param('vv.test_2', label='Characteristic 2 curve', default='Enabled', values=['Disabled', 'Enabled'],
           active='vv.mode', active_value=['Normal'])
info.param('vv.test_2_t_r', label='Settling time min (t) for curve 2', default=1.0,
           active='vv.test_2', active_value=['Enabled'])
info.param('vv.test_3', label='Characteristic 3 curve', default='Enabled', values=['Disabled', 'Enabled'],
           active='vv.mode', active_value=['Normal'])
info.param('vv.test_3_t_r', label='Settling time max (t) for curve 3', default=90.0,
           active='vv.test_3', active_value=['Enabled'])
info.param('vv.irr', label='Power Levels iteration', default='All', values=['100%', '66%', '20%', 'All'],
           active='vv.mode', active_value=['Normal'])
info.param('vv.vref', label='Voltage reference iteration', default='All', values=['100%', '95%', '105%', 'All'],
           active='vv.mode', active_value=['Normal'])
info.param('vv.imbalance_fix', label='Use minimum fix requirements from table 24 ?',
           default='No', values=['Yes', 'No'], active='vv.mode', active_value=['Imbalanced grid'])

# EUT general parameters
info.param_group('eut', label='EUT Parameters', glob=True)
info.param('eut.phases', label='Phases', default='Single Phase', values=['Single phase', 'Split phase', 'Three phase'])
info.param('eut.s_rated', label='Apparent power rating (VA)', default=10000.0)
info.param('eut.p_rated', label='Output power rating (W)', default=8000.0)
info.param('eut.p_min', label='Minimum Power Rating(W)', default=1000.)
info.param('eut.var_rated', label='Output var rating (vars)', default=2000.0)
info.param('eut.v_nom', label='Nominal AC voltage (V)', default=120.0, desc='Nominal voltage for the AC simulator.')
info.param('eut.v_low', label='Minimum AC voltage (V)', default=116.0)
info.param('eut.v_high', label='Maximum AC voltage (V)', default=132.0)
info.param('eut.v_in_nom', label='V_in_nom: Nominal input voltage (Vdc)', default=400)
info.param('eut.f_nom', label='Nominal AC frequency (Hz)', default=60.0)
info.param('eut.f_max', label='Maximum frequency in the continuous operating region (Hz)', default=66.)
info.param('eut.f_min', label='Minimum frequency in the continuous operating region (Hz)', default=56.)
info.param('eut.imbalance_resp', label='EUT response to phase imbalance is calculated by:',
           default='EUT response to the average of the three-phase effective (RMS)',
           values=['EUT response to the individual phase voltages',
                   'EUT response to the average of the three-phase effective (RMS)',
                   'EUT response to the positive sequence of voltages'])



# Other equipment parameters
der.params(info)
gridsim.params(info)
pvsim.params(info)
das.params(info)
hil.params(info)

# Add the SIRFN logo
info.logo('sirfn.png')

def script_info():
    return info


if __name__ == "__main__":

    # stand alone invocation
    config_file = None
    if len(sys.argv) > 1:
        config_file = sys.argv[1]

    params = None

    test_script = script.Script(info=script_info(), config_file=config_file, params=params)
    test_script.log('log it')

    run(test_script)
