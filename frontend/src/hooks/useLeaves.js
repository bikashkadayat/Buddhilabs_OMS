import { useState, useCallback } from 'react';
import { leaveService } from '../services/leaveService';
import { useAuth } from './useAuth';

export const useLeaves = () => {
  const [leaves, setLeaves] = useState([]);
  const [balances, setBalances] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const { user } = useAuth();

  const fetchLeaves = useCallback(async () => {
    setLoading(true);
    try {
      const response = await leaveService.getAll();
      setLeaves(response.data);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to fetch leave data');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchBalances = useCallback(async () => {
    setLoading(true);
    try {
      const response = await leaveService.getBalances();
      setBalances(response.data);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to fetch balance data');
    } finally {
      setLoading(false);
    }
  }, []);

  const applyLeave = async (data) => {
    setLoading(true);
    try {
      const response = await leaveService.create(data, user.name);
      await fetchLeaves();
      return response.data;
    } catch (err) {
      setError(err.message || 'Failed to apply for leave');
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const updateLeaveStatus = async (id, status, remarks = '') => {
    setLoading(true);
    try {
      const response = await leaveService.updateStatus(id, status, remarks);
      await fetchLeaves();
      return response.data;
    } catch (err) {
      setError(err.message || 'Failed to update leave string');
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return { leaves, balances, loading, error, fetchLeaves, fetchBalances, applyLeave, updateLeaveStatus };
};
